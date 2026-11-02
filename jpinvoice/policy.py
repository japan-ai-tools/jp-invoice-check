"""Your company's own rules (社内規則) and a fingerprint of the rules the checker uses.

Why (feedback on Qiita, 2026-11-01):
- A check is only worth something if the AI that is being checked cannot change it. So the company rule is
  loaded once, when a person starts the checker (--policy FILE or the JPINVOICE_POLICY variable). Nothing the AI
  sends in a tool call can change it, and a "rounding" field inside the invoice is ignored while a policy is set.
- Decide one rounding method for the company and write it down. Then one check compares the invoice with that rule,
  and a mismatch with a supplier's invoice is explained at once (which method they used, and how many yen it moves).

Policy file (JSON):
  {"name": "Example Co. invoice rules", "rounding": "floor", "price_basis": "exclusive"}
"""
import hashlib
import json
import os
from pathlib import Path

ROUNDINGS = ("floor", "half_up", "ceil")
BASES = ("exclusive", "inclusive")
ROUNDING_JA = {"floor": "切捨て", "half_up": "四捨五入", "ceil": "切上げ"}
BASIS_JA = {"exclusive": "税抜", "inclusive": "税込"}
ENV = "JPINVOICE_POLICY"


def validate(p):
    if not isinstance(p, dict):
        raise ValueError("policy must be a JSON object")
    if p.get("rounding") not in ROUNDINGS:
        raise ValueError(f"policy.rounding must be one of {ROUNDINGS}")
    if p.get("price_basis", "exclusive") not in BASES:
        raise ValueError(f"policy.price_basis must be one of {BASES}")
    return {"name": str(p.get("name") or "company policy")[:120], "rounding": p["rounding"],
            "price_basis": p.get("price_basis", "exclusive")}


def load(path=None):
    """Load the policy from a path, or from $JPINVOICE_POLICY. Returns None if neither is given."""
    path = path or os.environ.get(ENV)
    if not path:
        return None
    return validate(json.loads(Path(path).read_text(encoding="utf-8")))


def _canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def fingerprint(policy=None):
    """sha256 of the checking code (line endings normalized) plus the policy.

    Compare `code_sha256` with the value published for your version (README, "Fingerprint").
    If it differs, the checker was edited after it was installed.
    """
    here = Path(__file__).resolve().parent
    code = hashlib.sha256()
    for name in ("check.py", "policy.py"):
        code.update(name.encode() + b"\0" + (here / name).read_bytes().replace(b"\r\n", b"\n"))
    from . import __version__
    out = {"version": __version__, "code_sha256": code.hexdigest()}
    if policy:
        out["policy"] = policy
        out["policy_sha256"] = hashlib.sha256(_canon(policy)).hexdigest()
    return out
