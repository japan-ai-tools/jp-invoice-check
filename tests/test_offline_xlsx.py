"""v0.3.0 of the offline page: reading .xlsx, the CSV export, the print stylesheet, and still no network.

The page's own code (between /*CORE-START*/ and /*CORE-END*/) is run with Node.js, the same way as
tests/test_offline_html.py. The .xlsx files are built here with Python's zipfile (and with openpyxl when it is
installed) and must give exactly the same findings as the equivalent CSV, in the page and in the Python checker.
When Playwright and a Chromium are installed, the real page is also opened in the browser: a file is chosen, the
result table is read, the CSV is downloaded, and every request the page makes is recorded.
"""
import base64
import csv
import datetime
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from xml.sax.saxutils import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jpinvoice.batch import check_csv  # noqa: E402
from test_offline_html import HEAD, PAGE, SAMPLE, _random_csv, _read_page  # noqa: E402

RUNNER = r"""
const core = require(process.argv[2]);
const cases = JSON.parse(require('fs').readFileSync(process.argv[3], 'utf8'));
const bytes = c => c.b64 != null ? new Uint8Array(Buffer.from(c.b64, 'base64')) : c.text;
const full = rep => ({summary: rep.summary, source: rep.source, skipped: rep.skipped_rows.map(p => p.row),
  results: rep.results.map(r => ({id: r.invoice_id, row: r.row, ok: r.ok, issuer: r.issuer || '',
    input_errors: r.input_errors,
    found: r.errors.concat(r.warnings).map(m => [m.level, m.code, m.message_ja, m.correct_ja || ''])}))});
(async () => {
  const out = [];
  for (const c of cases) {
    try {
      if (c.op === 'xlsx') out.push(full(await core.checkXlsx(bytes(c), c.policy, c.role)));
      else if (c.op === 'csv') out.push(full(core.checkCsv(bytes(c), c.policy, c.role)));
      else if (c.op === 'file') out.push(full(await core.checkFile(bytes(c), c.name, c.policy, c.role)));
      else if (c.op === 'rows') out.push(await core.readXlsx(bytes(c)));
      else if (c.op === 'export') {
        const rep = c.xlsx ? await core.checkXlsx(bytes(c), c.policy, c.role) : core.checkCsv(bytes(c), c.policy, c.role);
        out.push({b64: Buffer.from(core.findingsCsv(rep), 'utf8').toString('base64'), html: core.reportTable(rep)});
      }
      else if (c.op === 'serial') out.push(c.values.map(v => core.serialToYmd(v[0], v[1])));
      else if (c.op === 'format') out.push(c.values.map(v => core.formatKind(v[0], v[1])));
      else throw new Error('unknown op');
    } catch (e) { out.push({error: String(e && e.message)}); }
  }
  process.stdout.write(JSON.stringify(out));
})();
"""

# ---- a small .xlsx writer for the tests (zipfile only) ----
# cellXfs used below: 0 General, 1 built-in date (14), 2 built-in percent (9), 3 custom date, 4 custom #,##0,
# 5 custom Japanese era date, 6 custom percent
STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
          '<numFmts count="4"><numFmt numFmtId="164" formatCode="yyyy&quot;年&quot;m&quot;月&quot;d&quot;日&quot;"/>'
          '<numFmt numFmtId="165" formatCode="#,##0"/>'
          '<numFmt numFmtId="166" formatCode="[$-411]ggge&quot;年&quot;m&quot;月&quot;d&quot;日&quot;"/>'
          '<numFmt numFmtId="167" formatCode="0.0%"/></numFmts>'
          '<fonts count="1"><font><sz val="11"/><name val="游ゴシック"/></font></fonts>'
          '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
          '<borders count="1"><border/></borders>'
          '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
          '<cellXfs count="7"><xf numFmtId="0" xfId="0"/><xf numFmtId="14" xfId="0" applyNumberFormat="1"/>'
          '<xf numFmtId="9" xfId="0"/><xf numFmtId="164" xfId="0"/><xf numFmtId="165" xfId="0"/>'
          '<xf numFmtId="166" xfId="0"/><xf numFmtId="167" xfId="0"/></cellXfs></styleSheet>')
S_DATE, S_PCT, S_DATE_JA, S_COMMA, S_ERA, S_PCT1 = 1, 2, 3, 4, 5, 6


def serial(y, m, d):
    return (datetime.date(y, m, d) - datetime.date(1899, 12, 30)).days


def col_name(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def S(text):
    return ("s", text)


def RICH(runs, furigana=None):
    return ("rich", runs, furigana)


def INLINE(text):
    return ("inline", text)


def N(value, style=0):
    return ("n", value, style)


def make_xlsx(rows, stored=("xl/sharedStrings.xml",), deflated=True, sheet_names=("明細",), row_numbers=None,
              first_sheet_path="worksheets/sheet1.xml"):
    """rows: list of rows; a row is a list of cell specs (None = no cell at all). Returns the bytes of an .xlsx."""
    sst, index = [], {}

    def shared(xml):
        if xml not in index:
            index[xml] = len(sst)
            sst.append(xml)
        return index[xml]

    body = []
    for ri, row in enumerate(rows):
        rn = row_numbers[ri] if row_numbers else ri + 1
        cells = []
        for ci, c in enumerate(row):
            ref = f"{col_name(ci)}{rn}"
            if c is None:
                continue
            kind = c[0]
            if kind == "s":
                cells.append(f'<c r="{ref}" t="s"><v>{shared("<t xml:space=" + chr(34) + "preserve" + chr(34) + ">" + escape(c[1]) + "</t>")}</v></c>')
            elif kind == "rich":
                xml = "".join(f'<r><rPr><b/><sz val="11"/></rPr><t>{escape(t)}</t></r>' for t in c[1])
                if c[2]:
                    xml += f'<rPh sb="0" eb="2"><t>{escape(c[2])}</t></rPh><phoneticPr fontId="1"/>'
                cells.append(f'<c r="{ref}" t="s"><v>{shared(xml)}</v></c>')
            elif kind == "inline":
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(c[1])}</t></is></c>')
            elif kind == "n":
                cells.append(f'<c r="{ref}" s="{c[2]}"><v>{c[1]}</v></c>')
            elif kind == "raw":  # ("raw", attributes, inner xml)
                cells.append(f'<c r="{ref}"{c[1]}>{c[2]}</c>')
            elif kind == "blank":  # a formatted cell with nothing in it
                cells.append(f'<c r="{ref}" s="{c[1]}"/>')
            else:
                raise ValueError(kind)
        body.append(f'<row r="{rn}" spans="1:14">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    sheet = (f'{head}<worksheet {ns}><dimension ref="A1:N{len(rows)}"/><sheetViews><sheetView workbookViewId="0"/></sheetViews>'
             f'<cols><col min="1" max="14" width="12"/></cols><sheetData>{"".join(body)}</sheetData>'
             '<phoneticPr fontId="1"/></worksheet>')
    other = f'{head}<worksheet {ns}><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>別のシート</t></is></c></row></sheetData></worksheet>'
    sheets = "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(sheet_names))
    workbook = (f'{head}<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f'<workbookPr defaultThemeVersion="166925"/><sheets>{sheets}</sheets></workbook>')
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    rels = [f'<Relationship Id="rId1" Type="{rel}/worksheet" Target="{first_sheet_path}"/>']
    rels += [f'<Relationship Id="rId{i + 1}" Type="{rel}/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(1, len(sheet_names))]
    rels += [f'<Relationship Id="rId90" Type="{rel}/styles" Target="styles.xml"/>',
             f'<Relationship Id="rId91" Type="{rel}/sharedStrings" Target="sharedStrings.xml"/>']
    members = {
        "[Content_Types].xml": f'{head}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>',
        "_rels/.rels": f'{head}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": f'{head}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{"".join(rels)}</Relationships>',
        "xl/styles.xml": STYLES,
        "xl/sharedStrings.xml": f'{head}<sst {ns} count="{len(sst)}" uniqueCount="{len(sst)}">{"".join("<si>" + x + "</si>" for x in sst)}</sst>',
        "xl/" + first_sheet_path.lstrip("/").replace("xl/", "", 1): sheet,
    }
    for i in range(1, len(sheet_names)):
        members[f"xl/worksheets/sheet{i + 1}.xml"] = other
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in members.items():
            how = zipfile.ZIP_DEFLATED if deflated and name not in stored else zipfile.ZIP_STORED
            z.writestr(zipfile.ZipInfo(name, date_time=(2026, 11, 1, 0, 0, 0)), text.encode("utf-8"), compress_type=how)
    return buf.getvalue()


def csv_text(rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    for r in rows:
        w.writerow(r)
    return out.getvalue()


def handmade():
    """The same invoices twice: as CSV cells (text) and as .xlsx cells.

    Returns (csv rows, xlsx rows, the text the page should read from each xlsx cell)."""
    heads = HEAD.strip().split(",")
    pairs = [[(h, INLINE(h) if i == 3 else S(h)) for i, h in enumerate(heads)]]
    E = ("", None)

    def row(*cells):
        cells = list(cells)
        while len(cells) < 14:
            cells.append(E)
        pairs.append(cells)

    def t(text, how=S):
        return (text, how(text))

    def n(value, style=0, text=None):
        return (str(value) if text is None else text, N(value, style))

    d = serial(2026, 10, 28)
    # A-001: tax rounded per line item (the mistake the product is about). Date = a number with the built-in date format.
    row(t("A-001"), ("株式会社サンプル商事", RICH(["株式会社", "サンプル商事"], "カブシキガイシャ")), t("T1123456789012"),
        ("2026/10/28", N(d, S_DATE)), t("見本株式会社", INLINE), t("税抜"), t("コピー用紙"), n(1999), n(10), E,
        n(5997), n(597), n(3579), n(285))
    row(t("A-001"), E, E, E, E, E, t("トナー"), n(2999), n(10))
    row(t("A-001"), ("", ("blank", S_DATE)), E, E, E, E, t("ボールペン", INLINE), n(999), n(10))
    row(t("A-001"), E, E, E, E, E, t("お茶(飲料)"), n(1234), n(8), t("※"))
    row(t("A-001"), E, E, E, E, E, t("菓子"), n(2345), n(8), ("TRUE", ("raw", ' t="b"', "<v>1</v>")))
    # B-002: correct. Custom date format, 1,999 as a number shown with commas, the rate as a percent cell (0.1 = 10%).
    row(t("B-002"), t("株式会社サンプル商事"), t("T7123456789012"), ("2026/10/28", N(d, S_DATE_JA)), t("見本株式会社"), t("税抜"),
        t("コピー用紙"), ("1,999", N(1999, S_COMMA), "1999"), ("10%", N(0.1, S_PCT)), E, n(5997), n(599), n(3579), n(286))
    row(t("B-002"), E, E, E, E, E, t("トナー"), n(2999), ("10%", N("0.1", S_PCT1)))
    row(t("B-002"), E, E, E, E, E, t("ボールペン"), n(999), n(10))
    row(t("B-002"), E, E, E, E, E, t("お茶(飲料)"), n(1234), ("8%", N(0.08, S_PCT)), t("※"))
    row(t("B-002"), E, E, E, E, E, ("菓子", ("raw", ' t="str"', '<f>"菓"&amp;"子"</f><v>菓子</v>')), n(2345), n(8), t("※"))
    pairs.append([E] * 14)  # a blank row in the middle of the sheet
    # C-003: no buyer (an empty cell in the middle of the row), the date as text, characters that need escaping.
    row(t("C-003"), t("見本商店 <東京&大阪>"), t("Ｔ１１２３４５６７８９０１２"), t("令和8年10月28日"), E, t("税込"),
        t('ラベル"大", <A&B>', INLINE), t("￥1,234"), n(10), E, n(1234), n(100))
    # D-004: a date that is not in the calendar, and an amount that is not a number
    row(t("D-004"), t("株式会社見本"), t("T123"), t("2026/02/30"), t("見本株式会社"), E, t("紙"), t("abc"), n(10))
    # E-005: an amount with a fraction (stays as it is, so both ways report it as unreadable)
    row(t("E-005"), t("株式会社見本"), t("T1123456789012"), ("2026/10/28", N(d + 0.75, S_DATE)), t("見本株式会社"), E,
        t("紙"), n(1999.5), n(10))
    # a row without an invoice number: skipped
    row(E, t("見本"), E, E, E, E, t("紙"), n(100), n(10))
    # F-006: Japanese era date format; 8% without the mark; the total in a different order of magnitude
    row(t("F-006"), t("見本商店"), t("T-1123-4567-8901-2"), ("2026/10/28", N(d, S_ERA)), t("見本株式会社"), t("税抜"),
        t("お茶"), n(1000), n(8), E, E, E, n(1000), n(80))
    row(t("F-006"), E, E, E, E, E, t("紙"), ("1500", N("1.5E3")), n(10), E, n(1500), n(151))
    return [[c[0] for c in r] for r in pairs], [[c[1] for c in r] for r in pairs], [[c[-1] if len(c) == 3 else c[0] for c in r] for r in pairs]


def _py_codes(case):
    raw = base64.b64decode(case["b64"]) if case.get("b64") is not None else case["text"]
    rep = check_csv(raw, policy=case.get("policy"), role=case.get("role", "issue"))
    return {"summary": rep["summary"], "skipped": [p["row"] for p in rep["skipped_rows"]], "results": [
        {"id": r["invoice_id"], "row": r["row"], "ok": r["ok"], "input_error": bool(r["input_errors"]),
         "codes": sorted(m["code"] for m in r["errors"] + r["warnings"])} for r in rep["results"]]}


def _codes(js):
    return {"summary": js["summary"], "skipped": js["skipped"], "results": [
        {"id": r["id"], "row": r["row"], "ok": r["ok"], "input_error": bool(r["input_errors"]),
         "codes": sorted(f[1] for f in r["found"])} for r in js["results"]]}


def _without_source(js):
    return {k: v for k, v in js.items() if k != "source"}


POLICIES = [(None, "issue")] + [({"name": "p", "rounding": r, "price_basis": b}, role)
                                for r in ("floor", "half_up", "ceil") for b in ("exclusive", "inclusive")
                                for role in ("issue", "receive")]


class NodeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("Node.js is not installed")
        m = re.search(r"/\*CORE-START\*/(.*)/\*CORE-END\*/", _read_page(), re.S)
        cls.tmp = tempfile.mkdtemp()
        cls.core = os.path.join(cls.tmp, "core.js")
        with open(cls.core, "w", encoding="utf-8") as f:
            f.write(m.group(1))
        cls.runner = os.path.join(cls.tmp, "runner.js")
        with open(cls.runner, "w", encoding="utf-8") as f:
            f.write(RUNNER)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def node(self, cases):
        path = os.path.join(self.tmp, "cases.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cases, f, ensure_ascii=False)
        res = subprocess.run(["node", self.runner, self.core, path], capture_output=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr.decode("utf-8", "replace"))
        out = json.loads(res.stdout.decode("utf-8"))
        self.assertEqual(len(out), len(cases))
        return out

    def same_as_csv(self, xlsx, text, policies=POLICIES):
        """The .xlsx through the page == the CSV through the page (every message) == the CSV through Python (codes)."""
        b64 = base64.b64encode(xlsx).decode()
        cases = []
        for policy, role in policies:
            cases.append({"op": "xlsx", "b64": b64, "policy": policy, "role": role})
            cases.append({"op": "csv", "text": text, "policy": policy, "role": role})
        got = self.node(cases)
        for i in range(0, len(got), 2):
            x, c, case = got[i], got[i + 1], cases[i + 1]
            self.assertNotIn("error", x, x)
            self.assertNotIn("error", c, c)
            self.assertEqual(_without_source(x), _without_source(c), f"policy={case['policy']} role={case['role']}")
            self.assertEqual(_codes(x), _py_codes(case), f"policy={case['policy']} role={case['role']}")
        return got


class TestXlsx(NodeCase):
    def test_handmade_xlsx_gives_the_same_findings_as_the_csv(self):
        rows_csv, rows_xlsx, _ = handmade()
        xlsx = make_xlsx(rows_xlsx, sheet_names=("明細", "メモ"))
        with zipfile.ZipFile(io.BytesIO(xlsx)) as z:  # the file really has both kinds of members
            kinds = {i.filename: i.compress_type for i in z.infolist()}
        self.assertEqual(kinds["xl/sharedStrings.xml"], zipfile.ZIP_STORED)
        self.assertEqual(kinds["xl/worksheets/sheet1.xml"], zipfile.ZIP_DEFLATED)
        got = self.same_as_csv(xlsx, csv_text(rows_csv))
        first = got[0]
        self.assertEqual(first["source"], {"kind": "xlsx", "sheet": "明細", "sheets": 2})
        by_id = {r["id"]: r for r in first["results"]}
        self.assertEqual(list(by_id), ["A-001", "B-002", "C-003", "D-004", "E-005", "F-006"])
        self.assertEqual([f[1] for f in by_id["A-001"]["found"]], ["E_TAX_LINE_ROUNDING", "E_TAX_LINE_ROUNDING", "W_REGNO_CHECK_DIGIT"])
        self.assertEqual(by_id["A-001"]["issuer"], "株式会社サンプル商事")  # rich text runs joined, furigana left out
        self.assertEqual(by_id["B-002"]["found"], [])  # date serial, 1,999 and 10% cells were all understood
        self.assertTrue(by_id["B-002"]["ok"])
        self.assertIn("E_BUYER_MISSING", [f[1] for f in by_id["C-003"]["found"]])
        self.assertNotIn("E_DATE_INVALID", [f[1] for f in by_id["C-003"]["found"]])
        self.assertTrue(by_id["D-004"]["input_errors"])
        self.assertTrue(by_id["E-005"]["input_errors"])
        self.assertEqual(by_id["C-003"]["row"], 13)  # the blank row 12 is counted, as in Excel
        self.assertEqual(first["skipped"], [16])

    def test_rows_keep_their_columns(self):
        _, rows_xlsx, rows_read = handmade()
        want = [[c for c in r] for r in rows_read]
        for r in want:
            while r and r[-1] == "":
                r.pop()
        # E-005's date cell has a time part (serial + 0.75): still the same day
        variants = [make_xlsx(rows_xlsx), make_xlsx(rows_xlsx, stored=()), make_xlsx(rows_xlsx, deflated=False),
                    make_xlsx(rows_xlsx, first_sheet_path="/xl/worksheets/data.xml")]
        got = self.node([{"op": "rows", "b64": base64.b64encode(v).decode()} for v in variants])
        for g in got:
            self.assertNotIn("error", g, g)
            self.assertEqual(g["sheet"], "明細")
            self.assertEqual(g["sheets"], 1)
            self.assertEqual(g["rows"], want)
        self.assertEqual(got[0]["rows"][3][:7], ["A-001", "", "", "", "", "", "ボールペン"])
        self.assertEqual(got[0]["rows"][11], [])

    def test_sample_csv_as_xlsx(self):
        with open(SAMPLE, encoding="utf-8") as f:
            text = f.read()
        self.same_as_csv(_xlsx_from_csv(text, random.Random(1)), text)

    def test_random_invoices_as_xlsx(self):
        rng = random.Random(20261111)
        for k in range(20):  # 20 files x 25 invoices = 500 invoices, as in the CSV parity test
            text = _random_csv(rng, 25)
            pol = None if k % 3 == 0 else {"name": "p", "rounding": rng.choice(["floor", "half_up", "ceil"]),
                                            "price_basis": rng.choice(["exclusive", "inclusive"])}
            xlsx = _xlsx_from_csv(text, rng, deflated=k % 4 != 0)
            self.same_as_csv(xlsx, text, policies=[(pol, rng.choice(["issue", "receive"]))])

    def test_openpyxl_workbook(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl is not installed")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "請求書明細"
        heads = HEAD.strip().split(",")
        day = datetime.date(2026, 10, 28)
        data = [
            ["A-001", "株式会社サンプル商事", "T1123456789012", day, "見本株式会社", "税抜", "コピー用紙", 1999, 10, None, 5997, 597, 3579, 285],
            ["A-001", None, None, None, None, None, "トナー", 2999, 10],
            ["A-001", None, None, None, None, None, "ボールペン", 999, 10],
            ["A-001", None, None, None, None, None, "お茶(飲料)", 1234, 8, "※"],
            ["A-001", None, None, None, None, None, "菓子", 2345, 8, "※"],
            ["B-002", "株式会社サンプル商事", "T7123456789012", datetime.datetime(2026, 10, 28, 9, 30), "見本株式会社", "税抜", "コピー用紙", 1999, 0.1, None, 5997, 599, 3579, 286],
            ["B-002", None, None, None, None, None, "トナー", 2999, 0.1],
            ["B-002", None, None, None, None, None, "ボールペン", 999, 0.1],
            ["B-002", None, None, None, None, None, "お茶(飲料)", 1234, 0.08, True],
            ["B-002", None, None, None, None, None, "菓子", 2345, 0.08, "※"],
            ["C-003", "見本商店", "T7123456789012", "令和8年10月28日", None, "税込", "紙 <A&B>", 1100, 10, None, 1100, 99],
        ]
        ws.append(heads)
        for r in data:
            ws.append(r)
        for r in range(7, 12):
            ws.cell(row=r, column=9).number_format = "0%"
        ws.cell(row=7, column=8).number_format = "#,##0"
        ws.cell(row=7, column=4).number_format = 'yyyy"年"m"月"d"日"'
        wb.create_sheet("メモ").append(["このシートは読まれない"])
        buf = io.BytesIO()
        wb.save(buf)

        def as_text(v):
            if v is None:
                return ""
            if v is True:
                return "TRUE"
            if isinstance(v, (datetime.date, datetime.datetime)):
                return v.strftime("%Y/%m/%d")
            if isinstance(v, float):
                return f"{round(v * 100)}%"
            return str(v)

        text = csv_text([heads] + [[as_text(v) for v in r] for r in data])
        got = self.same_as_csv(buf.getvalue(), text)
        self.assertEqual(got[0]["source"], {"kind": "xlsx", "sheet": "請求書明細", "sheets": 2})
        by_id = {r["id"]: r for r in got[0]["results"]}
        self.assertEqual([f[1] for f in by_id["A-001"]["found"]], ["E_TAX_LINE_ROUNDING", "E_TAX_LINE_ROUNDING", "W_REGNO_CHECK_DIGIT"])
        self.assertTrue(by_id["B-002"]["ok"])
        self.assertEqual([f[1] for f in by_id["C-003"]["found"]], ["E_TAX_MISMATCH", "E_BUYER_MISSING"])

    def test_date_serials(self):
        rng = random.Random(3)
        serials = [1, 59, 60, 61, 45000, 46323, 46323.999, 2958465] + [rng.randint(62, 80000) for _ in range(300)]
        want = ["1900/01/01", "1900/02/28", "1900/02/29", "1900/03/01"]
        want += [(datetime.date(1899, 12, 30) + datetime.timedelta(days=int(s))).strftime("%Y/%m/%d") for s in serials[4:]]
        cases = [{"op": "serial", "values": [[s, False] for s in serials]},
                 {"op": "serial", "values": [[0, False], [-5, False], [2958466, False], [0, True], [44861, True]]},
                 {"op": "format", "values": [[14, None], [0, None], [9, None], [1, None], [57, None], [164, "yyyy/mm/dd"],
                                             [164, "General"], [164, "#,##0"], [164, '0.00"円"'], [164, "0%"],
                                             [164, '[$-411]ge.m.d'], [164, '#,##0_ ;[Red]\\-#,##0\\ '], [164, '"day" 0'],
                                             [164, "0.00E+00"], [164, "@"]]}]
        got = self.node(cases)
        self.assertEqual(got[0], want)
        self.assertEqual(serial(2026, 10, 28), 46323)
        self.assertEqual(got[1], [None, None, None, "1904/01/01", "2026/10/28"])  # 1904 date system: 44861 + 1462 = 46323
        self.assertEqual(got[2], ["date", "", "percent", "", "date", "date", "", "", "", "percent", "date", "", "", "", ""])

    def test_date1904_workbook(self):
        _, rows, _ = handmade()
        xlsx = make_xlsx([rows[0], [S("Z-1"), S("見本"), S("T1123456789012"), N(serial(2026, 10, 28) - 1462, S_DATE)]])
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(xlsx)) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for i in zin.infolist():
                data = zin.read(i)
                if i.filename == "xl/workbook.xml":
                    data = data.replace(b"<workbookPr ", b'<workbookPr date1904="1" ')
                zout.writestr(i.filename, data)
        got = self.node([{"op": "rows", "b64": base64.b64encode(buf.getvalue()).decode()}])[0]
        self.assertEqual(got["rows"][1][3], "2026/10/28")

    def test_first_visible_sheet_is_read(self):
        _, rows, _ = handmade()
        xlsx = make_xlsx(rows, sheet_names=("明細", "メモ"))
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(xlsx)) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for i in zin.infolist():
                data = zin.read(i)
                if i.filename == "xl/workbook.xml":
                    data = data.replace('<sheet name="明細"'.encode(), '<sheet state="hidden" name="明細"'.encode())
                zout.writestr(i.filename, data)
        got = self.node([{"op": "rows", "b64": base64.b64encode(buf.getvalue()).decode()}])[0]
        self.assertEqual(got["sheet"], "メモ")
        self.assertEqual(got["rows"], [["別のシート"]])

    def test_xls_and_broken_files_are_explained_in_japanese(self):
        rows_csv, rows_xlsx, _ = handmade()
        good = make_xlsx(rows_xlsx)
        ole = bytes([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1]) + b"\0" * 600
        not_xlsx = io.BytesIO()
        with zipfile.ZipFile(not_xlsx, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", "<w:document/>")
        corrupt = bytearray(good)
        with zipfile.ZipFile(io.BytesIO(good)) as z:
            info = z.getinfo("xl/worksheets/sheet1.xml")
        start = info.header_offset + 30 + len(info.filename) + 10
        for i in range(start, start + 40):
            corrupt[i] ^= 0xFF
        b = lambda x: base64.b64encode(bytes(x)).decode()  # noqa: E731
        cases = [
            {"op": "file", "b64": b(ole), "name": "請求書.xls", "policy": None, "role": "issue"},
            {"op": "file", "b64": b("請求書番号,品名\nA,紙\n".encode("cp932")), "name": "old.XLS", "policy": None, "role": "issue"},
            {"op": "file", "b64": b(ole), "name": "名前だけxlsx.xlsx", "policy": None, "role": "issue"},
            {"op": "file", "b64": b(not_xlsx.getvalue()), "name": "文書.xlsx", "policy": None, "role": "issue"},
            {"op": "file", "b64": b(good[:len(good) // 2]), "name": "途中まで.xlsx", "policy": None, "role": "issue"},
            {"op": "file", "b64": b(corrupt), "name": "壊れた.xlsx", "policy": None, "role": "issue"},
            {"op": "file", "b64": b(b"\x00\x01\x02 not a spreadsheet"), "name": "x.xlsx", "policy": None, "role": "issue"},
            {"op": "file", "b64": b(make_xlsx([])), "name": "空.xlsx", "policy": None, "role": "issue"},
            {"op": "file", "b64": b(make_xlsx([[S("品名"), S("金額")], [S("紙"), N(1)]])), "name": "見出しなし.xlsx", "policy": None, "role": "issue"},
        ]
        got = self.node(cases)
        for g in got:
            self.assertIn("error", g, g)
            self.assertRegex(g["error"], r"[ぁ-んァ-ヶ一-龥]")  # the explanation is in Japanese
            self.assertNotIn("undefined", g["error"])
        for g in got[:3]:
            self.assertIn(".xls", g["error"])
            self.assertIn("保存し直して", g["error"])
        for g in got[3:7]:
            self.assertIn(".xlsx", g["error"])
            self.assertIn("読めませんでした", g["error"])
        self.assertIn("空", got[7]["error"])
        self.assertIn("請求書番号", got[8]["error"])
        # the same good file is read whatever its name says (the type is decided by the first bytes), and a CSV still works
        ok = self.node([{"op": "file", "b64": b(good), "name": "明細.csv", "policy": None, "role": "issue"},
                        {"op": "file", "b64": b(good), "name": "明細.xlsx", "policy": None, "role": "issue"},
                        {"op": "file", "b64": b(csv_text(rows_csv).encode("cp932")), "name": "明細.csv", "policy": None, "role": "issue"}])
        self.assertEqual(ok[0], ok[1])
        self.assertEqual(ok[2]["source"], {"kind": "csv"})
        self.assertEqual(_without_source(ok[2]), _without_source(ok[1]))


def _xlsx_from_csv(text, rng, deflated=True):
    """Any CSV as an .xlsx: whole numbers become number cells, the rest shared or inline strings, blanks no cell."""
    rows = []
    for r in csv.reader(io.StringIO(text)):
        cells = []
        for c in r:
            if c == "":
                cells.append(None)
            elif re.fullmatch(r"0|-?[1-9]\d{0,11}", c) and rng.random() < 0.8:
                cells.append(N(c))
            else:
                cells.append(INLINE(c) if rng.random() < 0.3 else S(c))
        rows.append(cells)
    return make_xlsx(rows, deflated=deflated, stored=("xl/workbook.xml",) if rng.random() < 0.5 else ())


class TestCsvExport(NodeCase):
    def _export(self, case):
        got = self.node([dict(case, op="export")])[0]
        self.assertNotIn("error", got, got)
        return base64.b64decode(got["b64"]), got["html"]

    def test_export_of_the_sample(self):
        with open(SAMPLE, encoding="utf-8") as f:
            text = f.read()
        raw, _ = self._export({"text": text, "policy": None, "role": "issue"})
        self.assertEqual(raw[:3], b"\xef\xbb\xbf")  # UTF-8 BOM: Excel opens it without garbled characters
        self.assertNotIn(b"\xef\xbb\xbf", raw[3:])
        body = raw[3:].decode("utf-8")
        self.assertTrue(body.endswith("\r\n"))
        self.assertNotIn("\n", body.replace("\r\n", ""))
        rows = list(csv.reader(io.StringIO(body, newline="")))
        self.assertEqual(rows[0], ["請求書番号", "行", "発行者", "判定", "種類", "誤りの名前", "説明", "正しい金額"])
        self.assertTrue(all(len(r) == 8 for r in rows))
        py = check_csv(text)
        want = []
        for r in py["results"]:
            found = r["errors"] + r["warnings"]
            if not found:
                want.append([r["invoice_id"], str(r["row"]), r["issuer"], "問題なし", "", "", "", ""])
            for m in found:
                verdict = "誤りあり" if not r["ok"] else "注意"
                want.append([r["invoice_id"], str(r["row"]), r["issuer"], verdict,
                             "誤り" if m["level"] == "error" else "注意", m["code"]])
        self.assertEqual([r[:len(w)] for r, w in zip(rows[1:], want)], want)  # same invoices, verdicts and codes as Python
        self.assertEqual(len(rows) - 1, len(want))
        a = [r for r in rows if r[0] == "A-001"]
        self.assertEqual([r[5] for r in a], ["E_TAX_LINE_ROUNDING", "E_TAX_LINE_ROUNDING", "W_REGNO_CHECK_DIGIT"])
        self.assertIn("正しくは 599円(切捨て)・600円(四捨五入)・600円(切上げ)", a[0][6])
        self.assertEqual(a[2][7], "")
        self.assertEqual(a[0][7], "599円(切捨て)・600円(四捨五入)・600円(切上げ)")
        self.assertEqual(a[1][7], "286円(切捨て)・286円(四捨五入)・287円(切上げ)")
        self.assertEqual([r for r in rows if r[0] == "B-002"][0][3:], ["問題なし", "", "", "", ""])

    def test_export_with_policy_unreadable_rows_and_awkward_text(self):
        text = (HEAD
                + 'P-1,"見本, ""商店""",T7123456789012,2026/10/28,見本株式会社,税抜,紙,1005,10,,1005,101,,\n'   # half up; policy = floor
                + "P-2,見本商店,T7123456789012,2026/10/28,見本株式会社,税抜,紙,1000,10,,1001,100,,\n"       # wrong total
                + "P-3,見本商店,T7123456789012,2026/10/28,見本株式会社,税抜,紙,abc,10,,,,,\n"               # unreadable
                + "=1+1,@見本,T7123456789012,2026/10/28,見本株式会社,税抜,紙,1000,10,,1000,100,,\n"          # looks like a formula
                + "-7,+見本,T7123456789012,2026/10/28,見本株式会社,税抜,紙,1000,10,,1000,100,,\n")
        policy = {"name": "p", "rounding": "floor", "price_basis": "exclusive"}
        raw, html = self._export({"text": text, "policy": policy, "role": "receive"})
        rows = list(csv.reader(io.StringIO(raw[3:].decode("utf-8"), newline="")))
        by = {}
        for r in rows[1:]:
            by.setdefault(r[0], []).append(r)
        p1 = by["P-1"]
        self.assertEqual(p1[0][2], '見本, "商店"')  # commas and quotes survive the round trip
        self.assertEqual([(r[3], r[4], r[5], r[7]) for r in p1], [("注意", "注意", "W_POLICY_ROUNDING", "100円(切捨て)")])
        self.assertEqual([(r[3], r[4], r[5], r[7]) for r in by["P-2"]], [("誤りあり", "誤り", "E_TOTAL_MISMATCH", "1000円")])
        self.assertEqual([(r[3], r[4], r[5]) for r in by["P-3"]], [("読めない", "読めない", "")])
        self.assertIn("abc", by["P-3"][0][6])
        self.assertIn("4行目", by["P-3"][0][6])  # the heading is line 1, so P-3 is on line 4
        # cells that Excel would run as a formula get a leading apostrophe
        self.assertEqual(by["'=1+1"][0][2], "'@見本")
        self.assertEqual(by["'-7"][0][2], "'+見本")
        for line in raw[3:].decode("utf-8").split("\r\n"):
            for cell in next(csv.reader([line]), []):
                self.assertNotRegex(cell, r"^[=+\-@]")
        # the on-screen table has the same verdicts, with a repeating header for printing
        self.assertIn("<thead>", html)
        self.assertEqual(html.count("<tr class='ng'>"), 2)
        self.assertEqual(html.count("<tr class='warn'>"), 1)

    def test_export_from_xlsx_equals_export_from_csv(self):
        rows_csv, rows_xlsx, _ = handmade()
        a, html_x = self._export({"xlsx": True, "b64": base64.b64encode(make_xlsx(rows_xlsx)).decode(), "policy": None, "role": "issue"})
        b, html_c = self._export({"text": csv_text(rows_csv), "policy": None, "role": "issue"})
        self.assertEqual(a, b)
        self.assertIn("Excelの行", html_x)
        self.assertIn("読んだシート: 「明細」", html_x)
        self.assertIn("CSVの行", html_c)


class TestPageV030(unittest.TestCase):
    def test_csp_and_no_network_code(self):
        page = _read_page()
        m = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', page)
        self.assertTrue(m)
        csp = m.group(1)
        self.assertIn("default-src 'none'", csp)
        for word in ("connect-src", "http:", "https:", "*", "form-action", "frame-src"):
            self.assertNotIn(word, csp)
        self.assertLess(page.index("Content-Security-Policy"), page.index("<script"))
        for word in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "importScripts", "import(",
                     "new Worker", "serviceWorker", "RTCPeerConnection", "<iframe", "<form", "<object", "<embed"):
            self.assertNotIn(word, page, word)
        self.assertNotRegex(page, r"<script[^>]+src=")
        self.assertNotRegex(page, r"<link\b")
        self.assertNotRegex(page, r"<img\b")
        self.assertNotRegex(page, r"url\(")
        self.assertNotRegex(page, r"@import")
        # the only URLs in the page are the source of the rule (shown as text) and XML namespace-free code
        urls = set(re.findall(r"https?://[^\s\"'<>)]+", page))
        self.assertEqual(urls, {"https://www.nta.go.jp/publication/pamph/shohi/shohizei_11.pdf"})

    def test_single_file_with_xlsx_csv_export_and_print(self):
        page = _read_page()
        self.assertIn('VERSION = "0.3.0-offline"', page)
        self.assertRegex(page, r'<input type="file" id="file" accept="[^"]*\.csv[^"]*\.xlsx')
        self.assertIn('DecompressionStream("deflate-raw")', page)
        self.assertIn('id="savecsv"', page)
        self.assertIn('id="save"', page)  # 結果をHTMLで保存 is still there
        self.assertIn("結果をHTMLで保存", page)
        self.assertIn("@media print", page)
        self.assertRegex(page, r"@page\{size:A4")
        self.assertRegex(page, r"\.noprint\{display:none")
        for ident in ('id="drop" class="noprint"', '<fieldset class="noprint">'):
            self.assertIn(ident, page)
        self.assertIn(".xls", page)


def _launch():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise unittest.SkipTest("Playwright is not installed")
    pw = sync_playwright().start()
    try:
        return pw, pw.chromium.launch()
    except Exception as e:  # no browser on this machine
        pw.stop()
        raise unittest.SkipTest(f"Chromium could not be started: {type(e).__name__}")


class TestInBrowser(unittest.TestCase):
    """The real page in Chromium, opened from disk as a buyer would."""

    @classmethod
    def setUpClass(cls):
        cls.pw, cls.browser = _launch()
        cls.tmp = tempfile.mkdtemp()
        rows_csv, rows_xlsx, _ = handmade()
        cls.csv_text = csv_text(rows_csv)
        cls.xlsx = os.path.join(cls.tmp, "請求書明細.xlsx")
        with open(cls.xlsx, "wb") as f:
            f.write(make_xlsx(rows_xlsx, sheet_names=("明細", "メモ")))
        cls.csv = os.path.join(cls.tmp, "請求書明細.csv")
        with open(cls.csv, "wb") as f:
            f.write(cls.csv_text.encode("cp932"))
        cls.xls = os.path.join(cls.tmp, "古い形式.xls")
        with open(cls.xls, "wb") as f:
            f.write(bytes([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1]) + b"\0" * 600)
        cls.broken = os.path.join(cls.tmp, "壊れた.xlsx")
        with open(cls.broken, "wb") as f:
            f.write(b"PK\x03\x04 this is not a real workbook")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.context = self.browser.new_context(accept_downloads=True, locale="ja-JP")
        self.page = self.context.new_page()
        self.requests, self.console = [], []
        self.page.on("request", lambda r: self.requests.append(r.url))
        self.page.on("console", lambda m: self.console.append(m.text))
        self.page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
        self.url = "file://" + PAGE
        self.page.goto(self.url)

    def tearDown(self):
        self.context.close()

    def _table(self):
        self.page.wait_for_selector("#out table")
        return self.page.eval_on_selector_all("#out tbody tr", """rows => rows.map(tr => ({
            id: tr.cells[0].textContent, row: tr.cells[1].textContent, verdict: tr.cells[3].textContent,
            codes: Array.from(tr.querySelectorAll('code')).map(c => c.textContent).sort()}))""")

    def _want(self, policy=None, role="issue"):
        rep = check_csv(self.csv_text, policy=policy, role=role)
        out = []
        for r in rep["results"]:
            verdict = "読めない" if r["input_errors"] else ("誤りあり" if not r["ok"] else ("注意" if r["warnings"] else "問題なし"))
            out.append({"id": r["invoice_id"], "row": str(r["row"]), "verdict": verdict,
                        "codes": sorted(m["code"] for m in r["errors"] + r["warnings"])})
        return out

    def _download(self, selector):
        with self.page.expect_download() as d:
            self.page.click(selector)
        with open(d.value.path(), "rb") as f:
            return d.value.suggested_filename, f.read()

    def test_xlsx_in_the_page_matches_python_and_nothing_is_sent(self):
        page = self.page
        self.assertTrue(page.is_disabled("#savecsv"))
        page.set_input_files("#file", self.xlsx)
        self.assertEqual(self._table(), self._want())
        self.assertIn("読んだシート: 「明細」", page.inner_text("#out"))
        self.assertIn("シートが 2 枚", page.inner_text("#out"))
        self.assertIn("Excelの行", page.inner_text("#out thead"))
        # a change of the settings re-checks the same file
        page.select_option("#rounding", "floor")
        page.select_option("#role", "receive")
        policy = {"name": "社内規則", "rounding": "floor", "price_basis": "exclusive"}
        self.assertEqual(self._table(), self._want(policy, "receive"))
        page.select_option("#rounding", "")
        page.select_option("#role", "issue")
        # the same data as a Shift_JIS CSV gives the same table
        from_xlsx = self._table()
        page.set_input_files("#file", self.csv)
        page.locator("#out thead", has_text="CSVの行").wait_for()  # (no wait_for_function: the page's CSP forbids eval)
        self.assertEqual(self._table(), from_xlsx)

        # CSV export: BOM, and the same rows as the table
        name, raw = self._download("#savecsv")
        self.assertEqual(name, "検査結果.csv")
        self.assertEqual(raw[:3], b"\xef\xbb\xbf")
        rows = list(csv.reader(io.StringIO(raw[3:].decode("utf-8"), newline="")))
        self.assertEqual(rows[0][:4], ["請求書番号", "行", "発行者", "判定"])
        self.assertEqual({(r[0], r[3]) for r in rows[1:]}, {(r["id"], r["verdict"]) for r in from_xlsx})
        self.assertEqual(sorted(r[5] for r in rows[1:] if r[5]), sorted(c for r in from_xlsx for c in r["codes"]))
        # the HTML export is still there
        name, raw = self._download("#save")
        self.assertEqual(name, "検査結果.html")
        self.assertIn("E_TAX_LINE_ROUNDING", raw.decode("utf-8"))

        # nothing but the page itself was requested (downloads are blob: URLs made inside the page), no CSP complaints
        outside = [u for u in self.requests if u != self.url and not u.startswith("blob:")]
        self.assertEqual(outside, [])
        self.assertFalse([u for u in self.requests if u.startswith(("http:", "https:", "ws:", "wss:"))])
        self.assertEqual([m for m in self.console if "Content Security Policy" in m or "pageerror" in m], [])

    def test_xls_and_broken_files_show_a_japanese_message(self):
        page = self.page
        page.set_input_files("#file", self.xls)
        page.wait_for_selector("#out p.ng")
        text = page.inner_text("#out")
        self.assertIn("読めませんでした", text)
        self.assertIn(".xls", text)
        self.assertIn("保存し直して", text)
        self.assertTrue(page.is_disabled("#savecsv"))
        self.assertTrue(page.is_disabled("#save"))
        page.set_input_files("#file", self.broken)
        page.locator("#out p.ng", has_text="として読めませんでした").wait_for()
        self.assertIn("Excel のファイル(.xlsx)として読めませんでした", page.inner_text("#out"))
        # and a good file after a bad one works
        page.set_input_files("#file", self.xlsx)
        self.assertEqual(self._table(), self._want())
        self.assertEqual([m for m in self.console if "pageerror" in m], [])

    def test_print_layout_hides_the_controls(self):
        page = self.page
        page.click("#sample")
        self._table()
        self.assertTrue(page.is_visible("#drop"))
        page.emulate_media(media="print")
        for sel in ("#drop", "fieldset", "#save", "#savecsv", "#print", "#sample", "#file"):
            self.assertFalse(page.is_visible(sel), sel)
        self.assertTrue(page.is_visible("#out table"))
        self.assertTrue(page.is_visible("h1"))
        self.assertEqual(page.eval_on_selector("#out thead", "e => getComputedStyle(e).display"), "table-header-group")
        # the table fits the printable width of A4 (210mm - 2 x 12mm margins = 186mm = 703px at 96dpi)
        page.set_viewport_size({"width": 703, "height": 1000})
        self.assertLessEqual(page.eval_on_selector("#out table", "e => e.getBoundingClientRect().right"), 704)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 704)
        pdf = page.pdf(prefer_css_page_size=True)
        self.assertEqual(pdf[:5], b"%PDF-")
        # A4 = 595.92 x 841.92 points (Chromium writes "595.91998 841.91998")
        boxes = re.findall(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", pdf)
        self.assertTrue(boxes)
        for w, h in boxes:
            self.assertAlmostEqual(float(w), 595.92, delta=1)
            self.assertAlmostEqual(float(h), 841.92, delta=1)


if __name__ == "__main__":
    unittest.main()
