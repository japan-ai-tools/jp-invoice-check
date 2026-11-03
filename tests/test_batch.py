import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jpinvoice.batch import check_csv, parse_csv, to_html  # noqa: E402

SAMPLE = os.path.join(ROOT, "examples", "invoices_sample.csv")


def codes(r):
    return [m["code"] for m in r["errors"] + r["warnings"]]


class TestBatch(unittest.TestCase):
    def setUp(self):
        with open(SAMPLE, "rb") as f:
            self.raw = f.read()

    def test_sample(self):
        rep = check_csv(self.raw)
        self.assertEqual(rep["summary"]["invoices"], 2)
        a, b = rep["results"]
        self.assertEqual(a["invoice_id"], "A-001")
        self.assertFalse(a["ok"])
        self.assertIn("E_TAX_LINE_ROUNDING", codes(a))
        self.assertEqual(b["invoice_id"], "B-002")
        self.assertTrue(b["ok"], codes(b))

    def test_shift_jis_same_result(self):
        sj = self.raw.decode("utf-8").encode("cp932")
        self.assertEqual(check_csv(sj)["summary"], check_csv(self.raw)["summary"])

    def test_english_header_and_bom(self):
        text = ("﻿invoice_id,issuer_name,registration_number,date,buyer_name,description,amount,rate,reduced,total_10,tax_10\n"
                "X1,Sample Co,T7123456789012,2026-10-28,Buyer Co,paper,1000,10,,1000,100\n")
        rep = check_csv(text.encode("utf-8"))
        self.assertTrue(rep["results"][0]["ok"], codes(rep["results"][0]))

    def test_bad_number_is_reported_not_crashed(self):
        text = "請求書番号,品名,金額,税率\nZ1,紙,千円,10\n"
        rep = check_csv(text.encode("utf-8"))
        self.assertFalse(rep["results"][0]["ok"])
        self.assertTrue(rep["results"][0]["input_errors"])

    def test_missing_id_column(self):
        with self.assertRaises(ValueError):
            parse_csv("品名,金額\n紙,100\n")

    def test_html_escapes(self):
        text = "請求書番号,発行者,品名,金額,税率\n<script>x</script>,<b>evil</b>,紙,100,10\n"
        page = to_html(check_csv(text.encode("utf-8")))
        self.assertNotIn("<script>x", page)
        self.assertIn("&lt;script&gt;", page)

    def test_cli_writes_html(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.html")
            p = subprocess.run([sys.executable, "-m", "jpinvoice.batch", SAMPLE, "--html", out],
                               cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(p.returncode, 1)  # A-001 has an error
            self.assertIn("誤りあり 1", p.stdout)
            with open(out, encoding="utf-8") as f:
                self.assertIn("E_TAX_LINE_ROUNDING", f.read())



class TestBatchDates(unittest.TestCase):
    def test_impossible_date_is_reported_not_crash(self):
        text = ("請求書番号,発行者,登録番号,取引日,買い手,品名,金額,税率,10%対象額,10%税額\n"
                "X-1,見本,T1123456789012,2026/02/30,見本株式会社,紙,1000,10,1000,100\n")
        r = check_csv(text)["results"][0]
        self.assertIn("E_DATE_INVALID", codes(r))

if __name__ == "__main__":
    unittest.main()
