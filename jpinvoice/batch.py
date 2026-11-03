"""Check many invoices at once from a CSV (one row per line item) and write a readable HTML report.

For accounting staff (経理): export the invoice lines from Excel or your accounting software as CSV, run

    python -m jpinvoice.batch invoices.csv --html report.html [--policy company_policy.json] [--role receive]

Columns (Japanese or English header; order is free). Invoice-level columns may be filled on every row or only on
the first row of each invoice:
  請求書番号 / invoice_id        (required) rows with the same value are one invoice
  発行者 / issuer_name
  登録番号 / registration_number
  取引日 / date                  2026-10-28, 2026/10/28, 令和8年10月28日
  買い手 / buyer_name
  税込税抜 / price_basis         税抜 / 税込 / exclusive / inclusive (default 税抜)
  品名 / description
  金額 / amount                  integer yen (commas and ¥ are allowed)
  税率 / rate                    10 or 8 (10%, ８ are allowed)
  軽減 / reduced                 ※, 1, true, ○ = the item is marked as reduced-rate
  10%対象額 / total_10, 10%税額 / tax_10, 8%対象額 / total_8, 8%税額 / tax_8   what the invoice says

The CSV may be UTF-8 (with or without BOM) or Shift_JIS (cp932, as saved by Japanese Excel).
Python standard library only. No network. Nothing leaves your PC.
"""
import argparse
import csv
import datetime
import html
import io
import json
import re
import sys
import unicodedata

from . import __version__
from . import policy as _policy
from .check import check_invoice

ALIASES = {
    "invoice_id": ("請求書番号", "請求書no", "請求書id", "伝票番号", "invoice_id", "invoice"),
    "issuer_name": ("発行者", "発行者名", "取引先", "請求元", "issuer_name", "issuer"),
    "registration_number": ("登録番号", "インボイス番号", "適格請求書発行事業者登録番号", "registration_number"),
    "date": ("取引日", "取引年月日", "日付", "date"),
    "buyer_name": ("買い手", "宛名", "請求先", "buyer_name", "buyer"),
    "price_basis": ("税込税抜", "税抜税込", "price_basis", "basis"),
    "description": ("品名", "内容", "摘要", "description", "item"),
    "amount": ("金額", "amount"),
    "rate": ("税率", "rate"),
    "reduced": ("軽減", "軽減税率", "reduced"),
    "total_10": ("10%対象額", "10%対象", "total_10"),
    "tax_10": ("10%税額", "10%消費税", "tax_10"),
    "total_8": ("8%対象額", "8%対象", "total_8"),
    "tax_8": ("8%税額", "8%消費税", "tax_8"),
}
_HEAD = {}
for _key, _names in ALIASES.items():
    for _n in _names:
        _HEAD[unicodedata.normalize("NFKC", _n).lower().replace(" ", "")] = _key

INVOICE_FIELDS = ("issuer_name", "registration_number", "date", "buyer_name", "price_basis",
                  "total_10", "tax_10", "total_8", "tax_8")
_TRUE = {"※", "*", "1", "true", "yes", "y", "○", "〇", "はい", "軽減", "対象"}


def read_text(data):
    """bytes -> str. UTF-8 (BOM ok) first, then cp932 (Excel in Japan)."""
    if isinstance(data, str):
        return data
    for enc in ("utf-8-sig", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("the CSV is neither UTF-8 nor Shift_JIS (cp932)")


def _cell(v):
    return unicodedata.normalize("NFKC", v or "").strip()


def _int(v, what, row):
    s = re.sub(r"[,¥円\s]", "", _cell(v))
    if s.endswith("%"):
        s = s[:-1]
    if s == "":
        return None
    if not re.fullmatch(r"-?\d+", s):
        raise ValueError(f"row {row}: {what} is not a whole number: {v!r}")
    return int(s)


def _basis(v):
    s = _cell(v).lower()
    if s in ("", "税抜", "税抜き", "外税", "exclusive"):
        return "exclusive"
    if s in ("税込", "税込み", "内税", "inclusive"):
        return "inclusive"
    raise ValueError(f"price basis must be 税抜 or 税込: {v!r}")


def _date(v):
    s = _cell(v)
    m = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return datetime.date(y, mo, d).isoformat()
        except ValueError:  # not a calendar date (e.g. 2026/02/30): let the checker report E_DATE_INVALID
            return s
    return s


def parse_csv(text):
    """Return (invoices, problems). invoices: list of (invoice_id, first_row, invoice dict)."""
    rows = list(csv.reader(io.StringIO(read_text(text))))
    if not rows:
        raise ValueError("the CSV is empty")
    cols = {}
    for i, h in enumerate(rows[0]):
        key = _HEAD.get(_cell(h).lower().replace(" ", ""))
        if key and key not in cols:
            cols[key] = i
    if "invoice_id" not in cols:
        raise ValueError("the CSV needs a 請求書番号 (invoice_id) column")
    groups, order, problems = {}, [], []
    for n, r in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in r):
            continue
        get = lambda k: r[cols[k]] if k in cols and cols[k] < len(r) else ""  # noqa: E731
        iid = _cell(get("invoice_id"))
        if not iid:
            problems.append({"row": n, "message_ja": "請求書番号が空の行は飛ばしました。", "message": "Row without invoice_id was skipped."})
            continue
        if iid not in groups:
            groups[iid] = {"row": n, "head": {}, "items": [], "errors": []}
            order.append(iid)
        g = groups[iid]
        for k in INVOICE_FIELDS:
            v = _cell(get(k))
            if v and k not in g["head"]:
                g["head"][k] = v
        try:
            amount = _int(get("amount"), "金額", n)
            rate = _int(get("rate"), "税率", n)
        except ValueError as e:
            g["errors"].append(str(e))
            continue
        if amount is None and rate is None and not _cell(get("description")):
            continue
        item = {"description": _cell(get("description")), "amount": amount, "rate": rate}
        if _cell(get("reduced")).lower() in _TRUE:
            item["reduced"] = True
        g["items"].append(item)
    out = []
    for iid in order:
        g = groups[iid]
        h = g["head"]
        inv = {"issuer": {"name": h.get("issuer_name", ""), "registration_number": h.get("registration_number", "")},
               "date": _date(h.get("date", "")), "buyer": {"name": h.get("buyer_name", "")}, "items": g["items"],
               "totals": []}
        try:
            inv["price_basis"] = _basis(h.get("price_basis", ""))
            for rate in (10, 8):
                amt = _int(h.get(f"total_{rate}"), f"{rate}%対象額", g["row"])
                tax = _int(h.get(f"tax_{rate}"), f"{rate}%税額", g["row"])
                if amt is not None or tax is not None:
                    inv["totals"].append({"rate": rate, "amount": amt, "tax": tax})
        except ValueError as e:
            g["errors"].append(str(e))
        out.append((iid, g["row"], inv, g["errors"]))
    return out, problems


def check_csv(text, policy=None, role="issue"):
    invoices, problems = parse_csv(text)
    results = []
    for iid, row, inv, errs in invoices:
        if errs:
            results.append({"invoice_id": iid, "row": row, "ok": False, "input_errors": errs,
                            "errors": [], "warnings": []})
            continue
        try:
            r = check_invoice(inv, policy=policy, role=role)
        except ValueError as e:
            results.append({"invoice_id": iid, "row": row, "ok": False, "input_errors": [str(e)],
                            "errors": [], "warnings": []})
            continue
        r.update({"invoice_id": iid, "row": row, "issuer": inv["issuer"]["name"], "input_errors": []})
        results.append(r)
    summary = {"invoices": len(results), "ok": sum(1 for r in results if r["ok"]),
               "with_errors": sum(1 for r in results if not r["ok"]),
               "with_warnings": sum(1 for r in results if r["ok"] and r["warnings"])}
    return {"summary": summary, "results": results, "skipped_rows": problems,
            "checker": _policy.fingerprint(policy)}


def to_html(report, title="適格請求書のまとめて検査"):
    e = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    s = report["summary"]
    fp = report.get("checker") or {}
    out = ["<!doctype html><html lang='ja'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<title>{e(title)}</title><style>",
           "body{font-family:system-ui,'Hiragino Sans','Meiryo',sans-serif;margin:24px;color:#222;background:#fff}",
           "table{border-collapse:collapse;width:100%;margin:12px 0}td,th{border:1px solid #ccc;padding:6px 8px;"
           "text-align:left;vertical-align:top}th{background:#f3f3f3}.ng{background:#fdecea}.warn{background:#fff7e0}"
           ".ok{background:#eaf6ec}code{font-size:90%}.small{color:#666;font-size:90%}",
           "</style></head><body>",
           f"<h1>{e(title)}</h1>",
           f"<p>請求書 {s['invoices']} 件 / 問題なし {s['ok']} 件 / <b>誤りあり {s['with_errors']} 件</b> / "
           f"注意だけ {s['with_warnings']} 件</p>",
           "<table><tr><th>請求書番号</th><th>CSVの行</th><th>発行者</th><th>結果</th><th>内容</th></tr>"]
    for r in report["results"]:
        if r["input_errors"]:
            cls, verdict = "ng", "読めない"
            body = "<br>".join(e(x) for x in r["input_errors"])
        else:
            cls = "ng" if not r["ok"] else ("warn" if r["warnings"] else "ok")
            verdict = {"ng": "誤りあり", "warn": "注意", "ok": "問題なし"}[cls]
            body = "<br>".join(f"<code>{e(m['code'])}</code> {e(m['message_ja'])}" for m in r["errors"] + r["warnings"])
        out.append(f"<tr class='{cls}'><td>{e(r['invoice_id'])}</td><td>{e(r['row'])}</td>"
                   f"<td>{e(r.get('issuer', ''))}</td><td>{verdict}</td><td>{body or '—'}</td></tr>")
    out.append("</table>")
    if report["skipped_rows"]:
        out.append("<p class='small'>飛ばした行: " + ", ".join(e(p["row"]) for p in report["skipped_rows"]) + "</p>")
    out.append("<p class='small'>確かめていないこと: 登録番号がいま登録されているか(国税庁のサイトで確認)・"
               "適格簡易請求書などの特例。根拠: 国税庁「適格請求書等保存方式の概要」"
               "(https://www.nta.go.jp/publication/pamph/shohi/shohizei_11.pdf)。</p>")
    out.append(f"<p class='small'>jp-invoice-check {e(__version__)} / 規則の指紋 {e(fp.get('sha256', ''))[:16]}</p>")
    out.append("</body></html>")
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m jpinvoice.batch",
                                description="Check many Japanese qualified invoices from a CSV and write an HTML report.")
    p.add_argument("csv", help="CSV file (one row per line item)")
    p.add_argument("--html", help="write the HTML report to this file")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    p.add_argument("--policy", help="your company rule file (JSON). Default: $JPINVOICE_POLICY")
    p.add_argument("--role", choices=["issue", "receive"], default="issue")
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    pol = _policy.load(a.policy)
    with open(a.csv, "rb") as f:
        report = check_csv(f.read(), policy=pol, role=a.role)
    if a.html:
        with open(a.html, "w", encoding="utf-8") as f:
            f.write(to_html(report))
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        s = report["summary"]
        print(f"請求書 {s['invoices']} 件: 問題なし {s['ok']} / 誤りあり {s['with_errors']} / 注意だけ {s['with_warnings']}"
              + (f" → {a.html}" if a.html else ""))
    return 0 if report["summary"]["with_errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
