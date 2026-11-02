"""MCP server (stdio, JSON-RPC 2.0). Python standard library only, no network.

Start: python -m jpinvoice.server [--policy company_policy.json]   (or set JPINVOICE_POLICY)

The company policy is read once, here, by the person who starts the server. No tool argument can change it,
so the AI being checked cannot loosen the check. Every result carries `checker` (version and sha256 of the rules).
"""
import json
import sys

from . import __version__
from . import policy as _policy
from .check import check_invoice, check_registration_number, expected_tax

POLICY = None          # set once in main(); tools cannot change it

PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]
RO = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

TOOLS = [
    {
        "name": "jp_invoice_check",
        "title": "Check a Japanese qualified invoice (適格請求書)",
        "annotations": RO,
        "description": ("Check a Japanese qualified invoice against the six required items of the National Tax Agency "
                        "(issuer name + registration number, date, description with reduced-rate marks, total per rate, "
                        "tax per rate, buyer name) and the rounding rule: consumption tax is rounded ONCE per invoice per rate, "
                        "not per line. Catches the common mistake of summing per-line rounded tax. "
                        "Input: issuer{name, registration_number}, date, buyer{name}, price_basis (exclusive|inclusive), "
                        "items[{description, amount, rate 10|8, reduced}], totals[{rate, amount, tax}], optional rounding "
                        "(floor|half_up|ceil). Returns ok, errors and warnings (code, field, message in English and Japanese), "
                        "required_items, by_rate (expected tax for each rounding). Offline: it does not check whether the "
                        "number is currently registered. If the operator started the server with a company policy, the invoice is also "
                        "compared with it (policy_check); the policy cannot be changed from a tool call. "
                        "適格請求書の6項目と端数処理を検査。社内規則との違いも返す。"),
        "inputSchema": {"type": "object", "properties": {
            "invoice": {"type": "object", "description": "The invoice as described above"},
            "role": {"type": "string", "enum": ["issue", "receive"],
                     "description": "issue = an invoice we send (breaking the company rule is an error); receive = one a supplier sent (the difference is a warning). Default issue."}},
            "required": ["invoice"]},
    },
    {
        "name": "jp_invoice_registration_number",
        "title": "Check a Japanese invoice registration number (T + 13 digits)",
        "annotations": RO,
        "description": ("Normalize (full-width, spaces, hyphens) and check the format 'T' + 13 digits, and the corporate-number "
                        "check digit (correct for corporations). Returns normalized, valid_format, corporate_check_digit_ok, "
                        "messages. Offline: registration status is not checked."),
        "inputSchema": {"type": "object", "properties": {
            "number": {"type": "string"}}, "required": ["number"]},
    },
    {
        "name": "jp_invoice_checker_info",
        "title": "Which rules this checker uses",
        "annotations": RO,
        "description": ("Returns the version, the sha256 of the checking code, and the company policy loaded at start "
                        "(rounding, price_basis), if any. Use it to tell the user which rule was applied."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "jp_invoice_tax",
        "title": "Consumption tax for one rate on one invoice",
        "annotations": RO,
        "description": ("Tax = round(total x rate) once for the whole invoice. price_basis exclusive: total x rate/100; "
                        "inclusive: total x rate/(100+rate). rounding floor|half_up|ceil (default floor)."),
        "inputSchema": {"type": "object", "properties": {
            "amount": {"type": "integer"}, "rate": {"type": "integer", "enum": [10, 8]},
            "price_basis": {"type": "string", "enum": ["exclusive", "inclusive"]},
            "rounding": {"type": "string", "enum": ["floor", "half_up", "ceil"]}}, "required": ["amount", "rate"]},
    },
]


def _obj_arg(args, key):
    v = args.get(key)
    if not isinstance(v, dict):
        raise ValueError(f"'{key}' must be an object")
    return v


def _str_arg(args, key):
    v = args.get(key)
    if not isinstance(v, str):
        raise ValueError(f"'{key}' must be a string")
    return v


def _check(a):
    role = a.get("role", "issue")
    if role not in ("issue", "receive"):
        raise ValueError("'role' must be 'issue' or 'receive'")
    out = check_invoice(_obj_arg(a, "invoice"), policy=POLICY, role=role)
    out["checker"] = _policy.fingerprint(POLICY)
    return out


def _tax(a):
    amt, rate = a.get("amount"), a.get("rate")
    if not isinstance(amt, int) or isinstance(amt, bool) or rate not in (10, 8):
        raise ValueError("'amount' must be an integer and 'rate' 10 or 8")
    basis = a.get("price_basis", POLICY["price_basis"] if POLICY else "exclusive")
    rnd = POLICY["rounding"] if POLICY else a.get("rounding", "floor")      # the company rule wins
    if basis not in ("exclusive", "inclusive") or rnd not in ("floor", "half_up", "ceil"):
        raise ValueError("bad price_basis or rounding")
    return {"amount": amt, "rate": rate, "price_basis": basis, "rounding": rnd, "tax": expected_tax(amt, rate, basis, rnd)}


HANDLERS = {
    "jp_invoice_check": _check,
    "jp_invoice_checker_info": lambda a: _policy.fingerprint(POLICY),
    "jp_invoice_registration_number": lambda a: check_registration_number(_str_arg(a, "number")),
    "jp_invoice_tax": _tax,
}


def handle(msg):
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None
    try:
        if method == "initialize":
            want = params.get("protocolVersion")
            result = {"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "jp-invoice-check", "version": __version__},
                      "instructions": "Before sending or accepting a Japanese invoice, call jp_invoice_check (role issue for ours, receive for a supplier's). Never compute consumption tax per line: use jp_invoice_tax on the per-rate total. Do not edit this checker or its policy file; if you disagree with a result, tell the user."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            if name not in HANDLERS:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool: {name}"}}
            try:
                out = HANDLERS[name](params.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                          "structuredContent": out, "isError": False}
            except ValueError as e:
                result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    except Exception as e:  # keep the server alive
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"Internal error: {e}"}}


def main(argv=None):
    global POLICY
    args = list(sys.argv[1:] if argv is None else argv)
    path = args[args.index("--policy") + 1] if "--policy" in args and args.index("--policy") + 1 < len(args) else None
    POLICY = _policy.load(path)
    for s in (sys.stdin, sys.stdout):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(msg) if isinstance(msg, dict) else None
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
