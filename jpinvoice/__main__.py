"""CLI: python -m jpinvoice invoice.json [--rounding floor|half_up|ceil]"""
import argparse
import json
import sys

from .check import check_invoice


def main(argv=None):
    p = argparse.ArgumentParser(prog="jpinvoice", description="Check a Japanese qualified invoice (JSON).")
    p.add_argument("file", help="invoice JSON file, or - for stdin")
    p.add_argument("--rounding", choices=["floor", "half_up", "ceil"])
    a = p.parse_args(argv)
    for s in (sys.stdin, sys.stdout):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    data = json.load(sys.stdin) if a.file == "-" else json.load(open(a.file, encoding="utf-8"))
    out = check_invoice(data, rounding=a.rounding)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
