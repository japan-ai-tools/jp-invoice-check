"""CLI: python -m jpinvoice invoice.json [--policy company_policy.json] [--role issue|receive] [--rounding ...]
       python -m jpinvoice --fingerprint [--policy company_policy.json]"""
import argparse
import json
import sys

from . import policy as _policy
from .check import check_invoice


def main(argv=None):
    p = argparse.ArgumentParser(prog="jpinvoice", description="Check a Japanese qualified invoice (JSON).")
    p.add_argument("file", nargs="?", help="invoice JSON file, or - for stdin")
    p.add_argument("--rounding", choices=["floor", "half_up", "ceil"], help="require one method (ignored when --policy is set)")
    p.add_argument("--policy", help="your company rule file (JSON). Default: $JPINVOICE_POLICY")
    p.add_argument("--role", choices=["issue", "receive"], default="issue",
                   help="issue = an invoice you send (rule mismatch is an error); receive = one you got (a warning)")
    p.add_argument("--fingerprint", action="store_true", help="print the version and sha256 of the rules in use")
    a = p.parse_args(argv)
    for s in (sys.stdin, sys.stdout):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    pol = _policy.load(a.policy)
    if a.fingerprint:
        print(json.dumps(_policy.fingerprint(pol), ensure_ascii=False, indent=2))
        return 0
    if not a.file:
        p.error("give an invoice file, or --fingerprint")
    data = json.load(sys.stdin) if a.file == "-" else json.load(open(a.file, encoding="utf-8"))
    out = check_invoice(data, rounding=None if pol else a.rounding, policy=pol, role=a.role)
    out["checker"] = _policy.fingerprint(pol)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
