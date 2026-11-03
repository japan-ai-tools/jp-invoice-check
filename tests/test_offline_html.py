"""The offline HTML version (offline/jp_invoice_check.html) must give the same answers as the Python checker.

The checking code inside the HTML (between /*CORE-START*/ and /*CORE-END*/) is run with Node.js on the same CSVs and
compared with jpinvoice.batch.check_csv: the verdict, the error/warning codes per invoice and the summary.
Skipped when Node.js is not installed. Also checks that the page cannot talk to the network (CSP) and has no
external links to scripts or styles.
"""
import base64
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jpinvoice.batch import check_csv  # noqa: E402

PAGE = os.path.join(ROOT, "offline", "jp_invoice_check.html")
SAMPLE = os.path.join(ROOT, "examples", "invoices_sample.csv")
HEAD = "請求書番号,発行者,登録番号,取引日,買い手,税込税抜,品名,金額,税率,軽減,10%対象額,10%税額,8%対象額,8%税額\n"

RUNNER = r"""
const core = require(process.argv[2]);
const cases = JSON.parse(require('fs').readFileSync(process.argv[3], 'utf8'));
const out = cases.map(c => {
  const input = c.b64 ? new Uint8Array(Buffer.from(c.b64, 'base64')) : c.text;
  try {
    const rep = core.checkCsv(input, c.policy, c.role);
    return {summary: rep.summary, results: rep.results.map(r => ({id: r.invoice_id, ok: r.ok,
      input_error: r.input_errors.length > 0, codes: r.errors.concat(r.warnings).map(m => m.code).sort()}))};
  } catch (e) { return {error: true}; }
});
process.stdout.write(JSON.stringify(out));
"""


def _read_page():
    with open(PAGE, encoding="utf-8") as f:
        return f.read()


def _py(case):
    raw = base64.b64decode(case["b64"]) if case.get("b64") else case["text"]
    try:
        rep = check_csv(raw, policy=case["policy"], role=case["role"])
    except ValueError:
        return {"error": True}
    return {"summary": rep["summary"], "results": [
        {"id": r["invoice_id"], "ok": r["ok"], "input_error": bool(r["input_errors"]),
         "codes": sorted(m["code"] for m in r["errors"] + r["warnings"])} for r in rep["results"]]}


def _random_csv(rng, n):
    """Invoices with the usual mistakes: per-line rounding, wrong totals, missing items, bad numbers and dates."""
    lines = [HEAD]
    regnos = ["T1123456789012", "T7123456789012", "Ｔ１１２３４５６７８９０１２", "T-1123-4567-8901-2", "T123", ""]
    dates = ["2026/10/28", "2026-10-28", "令和8年10月28日", "令和元年5月1日", "2026/02/30", "平成31年4月30日", ""]
    for i in range(n):
        iid = f"R-{i:03d}"
        basis = rng.choice(["税抜", "税込", "", "内税"])
        items = [(rng.randint(1, 30000), rng.choice([10, 10, 8])) for _ in range(rng.randint(0, 5))]
        exclusive = basis != "税込" and basis != "内税"
        sums = {}
        for a, r in items:
            sums[r] = sums.get(r, 0) + a
        tot = {}
        for r, s in sums.items():
            d = 100 if exclusive else 100 + r
            how = rng.choice(["floor", "half_up", "ceil", "line", "off"])
            if how == "floor":
                tax = s * r // d
            elif how == "ceil":
                tax = -(-s * r // d)
            elif how == "half_up":
                tax = (2 * s * r + d) // (2 * d)
            elif how == "line":
                tax = sum(a * r // d for a, rr in items if rr == r)
            else:
                tax = s * r // d + rng.choice([-3, 2, 7])
            amt = s if rng.random() > 0.15 else s + 1
            tot[r] = (amt, tax if rng.random() > 0.1 else "")
        if rng.random() < 0.1:
            tot[8] = (500, 40)
        first = True
        if not items:
            items = [(None, None)]
        for a, r in items:
            mark = "※" if (r == 8 and rng.random() > 0.15) or (r == 10 and rng.random() < 0.05) else ""
            amount = "" if a is None else rng.choice([str(a), f"{a:,}", f"¥{a}", str(a).translate(str.maketrans("0123456789", "０１２３４５６７８９"))])
            rate = "" if r is None else rng.choice([str(r), f"{r}%", "１０" if r == 10 else "８"])
            if rng.random() < 0.03:
                amount = "abc"
            if rng.random() < 0.03:
                rate = "5"
            desc = rng.choice(["コピー用紙", "お茶", "", "トナー, 黒", 'ラベル"大"']) if a is not None else ""
            if first:
                head = [iid, rng.choice(["株式会社見本", "", "見本商店"]), rng.choice(regnos), rng.choice(dates),
                        rng.choice(["見本株式会社", ""]), basis]
                totals = []
                for rr in (10, 8):
                    if rr in tot:
                        totals += [str(tot[rr][0]), str(tot[rr][1])]
                    else:
                        totals += ["", ""]
                first = False
            else:
                head = [iid, "", "", "", "", ""]
                totals = ["", "", "", ""]
            row = head + [desc, amount, rate, mark] + totals
            lines.append(",".join('"' + c.replace('"', '""') + '"' if ("," in c or '"' in c) else c for c in row) + "\n")
    lines.append(",見本,,,,,紙,100,10,,,,,\n")  # row without invoice id: skipped
    return "".join(lines)


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class TestOfflineParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        page = _read_page()
        m = re.search(r"/\*CORE-START\*/(.*)/\*CORE-END\*/", page, re.S)
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

    def _node(self, cases):
        path = os.path.join(self.tmp, "cases.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cases, f, ensure_ascii=False)
        res = subprocess.run(["node", self.runner, self.core, path], capture_output=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr.decode("utf-8", "replace"))
        return json.loads(res.stdout.decode("utf-8"))

    def _compare(self, cases):
        got = self._node(cases)
        for c, js in zip(cases, got):
            self.assertEqual(js, _py(c), f"policy={c['policy']} role={c['role']}")

    def test_sample_all_policies(self):
        with open(SAMPLE, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        cases = [{"b64": b64, "policy": None, "role": "issue"}]
        for rounding in ("floor", "half_up", "ceil"):
            for basis in ("exclusive", "inclusive"):
                for role in ("issue", "receive"):
                    cases.append({"b64": b64, "policy": {"name": "p", "rounding": rounding, "price_basis": basis}, "role": role})
        self._compare(cases)
        self.assertIn("E_TAX_LINE_ROUNDING", self._node(cases[:1])[0]["results"][0]["codes"])

    def test_shift_jis(self):
        with open(SAMPLE, encoding="utf-8") as f:
            text = f.read()
        b64 = base64.b64encode(text.encode("cp932")).decode()
        self._compare([{"b64": b64, "policy": None, "role": "issue"}])

    def test_random_invoices(self):
        rng = random.Random(20261103)
        cases = []
        for k in range(20):
            pol = None if k % 3 == 0 else {"name": "p", "rounding": rng.choice(["floor", "half_up", "ceil"]),
                                            "price_basis": rng.choice(["exclusive", "inclusive"])}
            cases.append({"text": _random_csv(rng, 25), "policy": pol, "role": rng.choice(["issue", "receive"])})
        self._compare(cases)

    def test_bad_csv(self):
        self._compare([{"text": "", "policy": None, "role": "issue"},
                       {"text": "品名,金額\nA,1\n", "policy": None, "role": "issue"}])


class TestOfflinePage(unittest.TestCase):
    def test_no_network(self):
        page = _read_page()
        self.assertIn("default-src 'none'", page)
        self.assertNotRegex(page, r"<script[^>]+src=")
        self.assertNotRegex(page, r"<link[^>]+href=")
        self.assertNotIn("fetch(", page)
        self.assertNotIn("XMLHttpRequest", page)

    def test_output_is_escaped(self):
        self.assertIn("function esc(", _read_page())
        self.assertNotIn("innerHTML = e.message", _read_page())


if __name__ == "__main__":
    unittest.main()
