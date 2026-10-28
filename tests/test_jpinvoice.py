import copy
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jpinvoice import check_invoice, check_registration_number, corporate_check_digit, expected_tax  # noqa: E402
from jpinvoice.server import handle  # noqa: E402

OK = json.load(open(os.path.join(ROOT, "examples", "ok.json"), encoding="utf-8"))


def codes(r):
    return [m["code"] for m in r["errors"] + r["warnings"]]


class TestTax(unittest.TestCase):
    def test_exclusive(self):
        self.assertEqual(expected_tax(5997, 10), 599)
        self.assertEqual(expected_tax(5997, 10, rounding="half_up"), 600)
        self.assertEqual(expected_tax(5997, 10, rounding="ceil"), 600)
        self.assertEqual(expected_tax(3579, 8), 286)

    def test_inclusive(self):
        self.assertEqual(expected_tax(1100, 10, "inclusive"), 100)
        self.assertEqual(expected_tax(1080, 8, "inclusive"), 80)
        self.assertEqual(expected_tax(1000, 10, "inclusive"), 90)  # 90.909...
        self.assertEqual(expected_tax(1000, 10, "inclusive", "half_up"), 91)

    def test_half_up_exact_half(self):
        self.assertEqual(expected_tax(5, 10, rounding="half_up"), 1)  # 0.5 -> 1
        self.assertEqual(expected_tax(5, 10, rounding="floor"), 0)


class TestRegistrationNumber(unittest.TestCase):
    def test_check_digit(self):
        self.assertEqual(corporate_check_digit("123456789012"), 7)

    def test_valid_and_normalized(self):
        r = check_registration_number("Ｔ７１２３-４５６７-８９０１２")
        self.assertEqual(r["normalized"], "T7123456789012")
        self.assertTrue(r["corporate_check_digit_ok"])
        self.assertIn("W_REGNO_NORMALIZED", [m["code"] for m in r["messages"]])

    def test_bad(self):
        self.assertFalse(check_registration_number("7123456789012")["valid_format"])
        self.assertFalse(check_registration_number("T712345678901")["valid_format"])
        self.assertEqual(check_registration_number("")["messages"][0]["code"], "E_REGNO_MISSING")
        r = check_registration_number("T1123456789012")
        self.assertTrue(r["valid_format"])
        self.assertFalse(r["corporate_check_digit_ok"])


class TestInvoice(unittest.TestCase):
    def test_ok(self):
        r = check_invoice(OK)
        self.assertTrue(r["ok"], r["errors"])
        self.assertTrue(all(r["required_items"].values()))

    def test_line_rounding_detected(self):
        d = copy.deepcopy(OK)
        d["totals"][0]["tax"] = 597  # 199 + 299 + 99
        r = check_invoice(d)
        self.assertFalse(r["ok"])
        self.assertIn("E_TAX_LINE_ROUNDING", codes(r))
        self.assertFalse(r["required_items"]["5_tax_per_rate"])

    def test_rounding_free_when_not_given(self):
        d = copy.deepcopy(OK)
        d.pop("rounding")
        d["totals"][0]["tax"] = 600  # half_up / ceil
        r = check_invoice(d)
        self.assertTrue(r["ok"])
        self.assertEqual(r["by_rate"][0]["matched_rounding"], "half_up")
        self.assertFalse(check_invoice(d, rounding="floor")["ok"])

    def test_wrong_tax(self):
        d = copy.deepcopy(OK)
        d["totals"][1]["tax"] = 300
        self.assertIn("E_TAX_MISMATCH", codes(check_invoice(d)))

    def test_reduced_marks(self):
        d = copy.deepcopy(OK)
        d["items"][3].pop("reduced")
        d["items"][0]["reduced"] = True
        c = codes(check_invoice(d))
        self.assertIn("E_REDUCED_NOT_MARKED", c)
        self.assertIn("E_REDUCED_RATE_MISMATCH", c)

    def test_missing_items(self):
        d = copy.deepcopy(OK)
        d["buyer"] = {}
        d["issuer"]["name"] = " "
        d["date"] = None
        d["totals"] = [d["totals"][0]]
        c = codes(check_invoice(d))
        for k in ("E_BUYER_MISSING", "E_ISSUER_NAME_MISSING", "E_DATE_MISSING", "E_TOTAL_BY_RATE_MISSING"):
            self.assertIn(k, c)

    def test_totals_and_rate(self):
        d = copy.deepcopy(OK)
        d["totals"][0]["amount"] = 6000
        d["items"].append({"description": "x", "amount": 100, "rate": 5})
        c = codes(check_invoice(d))
        self.assertIn("E_TOTAL_MISMATCH", c)
        self.assertIn("E_RATE_INVALID", c)

    def test_dates(self):
        for s, ok in (("令和8年10月28日", True), ("令和元年5月1日", True), ("2026/2/30", False), ("2026年2月28日", True)):
            d = copy.deepcopy(OK)
            d["date"] = s
            self.assertEqual(check_invoice(d)["required_items"]["2_transaction_date"], ok, s)
        d = copy.deepcopy(OK)
        d["date"] = {"from": "2026-10-01", "to": "2026-10-31"}
        self.assertTrue(check_invoice(d)["ok"])
        d["date"] = {"from": "2026-10-31", "to": "2026-10-01"}
        self.assertIn("E_DATE_INVALID", codes(check_invoice(d)))

    def test_inclusive_invoice(self):
        d = {"issuer": OK["issuer"], "date": "2026-10-28", "buyer": OK["buyer"], "price_basis": "inclusive",
             "items": [{"description": "a", "amount": 1100, "rate": 10}], "totals": [{"rate": 10, "amount": 1100, "tax": 100}]}
        self.assertTrue(check_invoice(d)["ok"])

    def test_bad_input_raises(self):
        with self.assertRaises(ValueError):
            check_invoice({"items": [{"description": "a", "amount": "100", "rate": 10}]})


class TestServerAndCli(unittest.TestCase):
    def call(self, name, args):
        return handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})["result"]

    def test_tools(self):
        r = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(len(r["result"]["tools"]), 3)
        self.assertTrue(self.call("jp_invoice_check", {"invoice": OK})["structuredContent"]["ok"])
        self.assertEqual(self.call("jp_invoice_tax", {"amount": 3579, "rate": 8})["structuredContent"]["tax"], 286)
        self.assertTrue(self.call("jp_invoice_tax", {"amount": 1, "rate": 5})["isError"])
        self.assertTrue(self.call("jp_invoice_check", {"invoice": "x"})["isError"])

    def test_cli(self):
        p = subprocess.run([sys.executable, "-m", "jpinvoice", os.path.join(ROOT, "examples", "line_rounding_mistake.json")],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 1)
        self.assertIn("E_TAX_LINE_ROUNDING", p.stdout)


if __name__ == "__main__":
    unittest.main()
