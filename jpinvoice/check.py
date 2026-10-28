"""Check a Japanese qualified invoice (適格請求書) given as a dict.

Rules used (National Tax Agency pamphlet, https://www.nta.go.jp/publication/pamph/shohi/shohizei_11.pdf):
  six required items: issuer name + registration number, transaction date, description (reduced-rate items marked),
  total per tax rate with the rate, consumption tax per tax rate, buyer name.
  Rounding: once per invoice, per tax rate (「一請求書当たり、税率ごとに1回ずつ」). The rounding method itself is free.
Registration number: "T" + 13 digits. For corporations the 13 digits are the corporate number (法人番号),
whose first digit is a check digit. Python standard library only, no network.
"""
import datetime
import math
import re
import unicodedata
from fractions import Fraction

RATES = (10, 8)
ROUNDINGS = ("floor", "half_up", "ceil")

_ERA = {"令和": 2018, "平成": 1988}


def _msg(level, code, field, en, ja):
    return {"level": level, "code": code, "field": field, "message": en, "message_ja": ja}


def _round(x, how):
    if how == "floor":
        return math.floor(x)
    if how == "ceil":
        return math.ceil(x)
    if how == "half_up":
        return math.floor(x + Fraction(1, 2)) if x >= 0 else -math.floor(-x + Fraction(1, 2))
    raise ValueError(f"unknown rounding: {how}")


def expected_tax(amount, rate, basis="exclusive", rounding="floor"):
    """Tax for one rate on one invoice. basis: 'exclusive' (税抜) or 'inclusive' (税込)."""
    a = Fraction(int(amount))
    r = Fraction(int(rate))
    raw = a * r / 100 if basis == "exclusive" else a * r / (100 + r)
    return _round(raw, rounding)


def corporate_check_digit(base12):
    """Check digit of a corporate number (法人番号) from its last 12 digits."""
    digits = [int(c) for c in base12][::-1]
    s = sum(d * (1 if i % 2 == 0 else 2) for i, d in enumerate(digits))
    return 9 - s % 9


def _norm(s):
    s = unicodedata.normalize("NFKC", str(s))
    return re.sub(r"[\s\-‐−ー―]", "", s)


def check_registration_number(value):
    """Return {normalized, valid_format, corporate_check_digit_ok, messages}."""
    out = {"input": value, "normalized": None, "valid_format": False, "corporate_check_digit_ok": None, "messages": []}
    if value is None or str(value).strip() == "":
        out["messages"].append(_msg("error", "E_REGNO_MISSING", "issuer.registration_number",
                                    "Registration number is missing (required item 1).",
                                    "登録番号がありません(記載事項1)。"))
        return out
    n = _norm(value)
    if n[:1] == "t":
        n = "T" + n[1:]
    if not re.fullmatch(r"T\d{13}", n):
        out["messages"].append(_msg("error", "E_REGNO_FORMAT", "issuer.registration_number",
                                    f"Registration number must be 'T' + 13 digits; got {value!r}.",
                                    f"登録番号は「T+13桁の数字」です。入力: {value!r}"))
        return out
    out["normalized"] = n
    out["valid_format"] = True
    if n != str(value):
        out["messages"].append(_msg("warning", "W_REGNO_NORMALIZED", "issuer.registration_number",
                                    f"Registration number was normalized to {n}.",
                                    f"登録番号を {n} に整えました(全角・空白・ハイフン)。"))
    ok = int(n[1]) == corporate_check_digit(n[2:])
    out["corporate_check_digit_ok"] = ok
    if not ok:
        out["messages"].append(_msg("warning", "W_REGNO_CHECK_DIGIT", "issuer.registration_number",
                                    "The first digit does not match the corporate-number check digit. "
                                    "If the issuer is a corporation, the number is wrong. "
                                    "Whether it is registered now is not checked (no network).",
                                    "先頭の1桁が法人番号の検査用数字と合いません。発行者が法人なら番号の誤りです。"
                                    "登録が今も有効かは確かめていません(通信しないため)。"))
    return out


def _parse_date(s):
    t = unicodedata.normalize("NFKC", str(s)).strip()
    m = re.fullmatch(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})日?", t)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(令和|平成)(\d{1,2}|元)年(\d{1,2})月(\d{1,2})日", t)
        if not m:
            return None
        n = 1 if m.group(2) == "元" else int(m.group(2))
        y, mo, d = _ERA[m.group(1)] + n, int(m.group(3)), int(m.group(4))
    try:
        return datetime.date(y, mo, d)
    except ValueError:
        return None


def _check_date(inv, msgs):
    d = inv.get("date")
    if d in (None, "", {}):
        msgs.append(_msg("error", "E_DATE_MISSING", "date", "Transaction date is missing (required item 2).",
                         "取引年月日がありません(記載事項2)。"))
        return False
    parts = [d.get("from"), d.get("to")] if isinstance(d, dict) else [d]
    ok = True
    for p in parts:
        if p is None or _parse_date(p) is None:
            msgs.append(_msg("error", "E_DATE_INVALID", "date", f"Date is not a real calendar date: {p!r}.",
                             f"日付が暦にない、または読めません: {p!r}"))
            ok = False
    if ok and isinstance(d, dict) and _parse_date(parts[0]) > _parse_date(parts[1]):
        msgs.append(_msg("error", "E_DATE_INVALID", "date", "Period 'from' is after 'to'.", "期間の始めが終わりより後です。"))
        ok = False
    return ok


def _int(v):
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError
    return v


def check_invoice(inv, rounding=None):
    """Check one invoice. Returns {ok, errors, warnings, required_items, by_rate}.

    inv = {
      "issuer": {"name": str, "registration_number": "T1234567890123"},
      "date": "2026-10-28" | "令和8年10月28日" | {"from": ..., "to": ...},
      "buyer": {"name": str},
      "price_basis": "exclusive" | "inclusive",          # amounts are before tax / tax included
      "items": [{"description": str, "amount": int, "rate": 10 | 8, "reduced": bool}],
      "totals": [{"rate": 10, "amount": int, "tax": int}],
      "rounding": "floor" | "half_up" | "ceil"            # optional; if absent any of the three is accepted
    }
    """
    if not isinstance(inv, dict):
        raise ValueError("invoice must be an object")
    msgs = []
    req = {}
    issuer = inv.get("issuer") or {}
    rn = check_registration_number(issuer.get("registration_number"))
    msgs += rn["messages"]
    has_name = bool(str(issuer.get("name") or "").strip())
    if not has_name:
        msgs.append(_msg("error", "E_ISSUER_NAME_MISSING", "issuer.name", "Issuer name is missing (required item 1).",
                         "発行者の氏名・名称がありません(記載事項1)。"))
    req["1_issuer_and_registration_number"] = has_name and rn["valid_format"]
    req["2_transaction_date"] = _check_date(inv, msgs)

    basis = inv.get("price_basis", "exclusive")
    if basis not in ("exclusive", "inclusive"):
        raise ValueError("price_basis must be 'exclusive' or 'inclusive'")
    rounding = rounding or inv.get("rounding")
    if rounding is not None and rounding not in ROUNDINGS:
        raise ValueError(f"rounding must be one of {ROUNDINGS}")

    items = inv.get("items") or []
    item_ok = bool(items)
    if not items:
        msgs.append(_msg("error", "E_ITEMS_MISSING", "items", "No line items (required item 3).", "取引の内容がありません(記載事項3)。"))
    sums, line_tax = {}, {}
    for i, it in enumerate(items):
        f = f"items[{i}]"
        if not str(it.get("description") or "").strip():
            msgs.append(_msg("error", "E_ITEM_DESCRIPTION_MISSING", f + ".description", "Line item has no description.",
                             "取引の内容(品名など)が空です。"))
            item_ok = False
        try:
            amt = _int(it.get("amount"))
            rate = _int(it.get("rate"))
        except ValueError:
            raise ValueError(f"{f}: amount and rate must be integers")
        if rate not in RATES:
            msgs.append(_msg("error", "E_RATE_INVALID", f + ".rate", f"Rate must be 10 or 8; got {rate}.",
                             f"税率は10か8です。入力: {rate}"))
            item_ok = False
            continue
        reduced = bool(it.get("reduced"))
        if rate == 8 and not reduced:
            msgs.append(_msg("error", "E_REDUCED_NOT_MARKED", f + ".reduced",
                             "8% item is not marked as reduced-rate (required item 3: mark reduced-rate items, e.g. ※).",
                             "8%の品目に軽減税率の対象である印(※など)がありません(記載事項3)。"))
            item_ok = False
        if rate == 10 and reduced:
            msgs.append(_msg("error", "E_REDUCED_RATE_MISMATCH", f + ".reduced", "Item is marked reduced-rate but has rate 10.",
                             "軽減税率の印があるのに税率が10%です。"))
            item_ok = False
        sums[rate] = sums.get(rate, 0) + amt
        line_tax.setdefault(rate, {r: 0 for r in ROUNDINGS})
        for r in ROUNDINGS:
            line_tax[rate][r] += expected_tax(amt, rate, basis, r)
    req["3_description_and_reduced_mark"] = item_ok

    totals = {}
    for j, t in enumerate(inv.get("totals") or []):
        try:
            totals[_int(t.get("rate"))] = t
        except ValueError:
            raise ValueError(f"totals[{j}].rate must be an integer")
    t_ok, x_ok, by_rate = bool(sums), bool(sums), []
    for rate in sorted(sums, reverse=True):
        exp = {r: expected_tax(sums[rate], rate, basis, r) for r in ROUNDINGS}
        row = {"rate": rate, "items_total": sums[rate], "expected_tax": exp, "line_rounding_tax": line_tax[rate],
               "stated_amount": None, "stated_tax": None, "matched_rounding": None}
        by_rate.append(row)
        t = totals.get(rate)
        if t is None:
            msgs.append(_msg("error", "E_TOTAL_BY_RATE_MISSING", "totals",
                             f"No total for the {rate}% rate (required items 4 and 5).",
                             f"{rate}%の合計と税額の行がありません(記載事項4・5)。"))
            t_ok = x_ok = False
            continue
        if t.get("amount") is None:
            msgs.append(_msg("error", "E_TOTAL_BY_RATE_MISSING", f"totals[{rate}].amount", f"No total amount for {rate}%.",
                             f"{rate}%の合計額がありません(記載事項4)。"))
            t_ok = False
        else:
            row["stated_amount"] = t["amount"]
            if t["amount"] != sums[rate]:
                msgs.append(_msg("error", "E_TOTAL_MISMATCH", f"totals[{rate}].amount",
                                 f"{rate}% total is {t['amount']} but the items add up to {sums[rate]}.",
                                 f"{rate}%の合計額 {t['amount']} が品目の合計 {sums[rate]} と合いません。"))
                t_ok = False
        if t.get("tax") is None:
            msgs.append(_msg("error", "E_TAX_MISSING", f"totals[{rate}].tax", f"No consumption tax for {rate}% (required item 5).",
                             f"{rate}%の消費税額がありません(記載事項5)。"))
            x_ok = False
            continue
        row["stated_tax"] = st = t["tax"]
        allowed = [rounding] if rounding else list(ROUNDINGS)
        hit = [r for r in allowed if exp[r] == st]
        if hit:
            row["matched_rounding"] = hit[0]
            continue
        x_ok = False
        if any(line_tax[rate][r] == st for r in allowed):
            msgs.append(_msg("error", "E_TAX_LINE_ROUNDING", f"totals[{rate}].tax",
                             f"{rate}% tax {st} equals the sum of per-line rounded taxes. The rule is to round once per "
                             f"invoice per rate: expected {', '.join(f'{exp[r]} ({r})' for r in allowed)}.",
                             f"{rate}%の税額 {st} は品目ごとに端数処理した合計です。端数処理は1つの請求書につき税率ごとに1回: "
                             f"正しくは {', '.join(f'{exp[r]}({r})' for r in allowed)}。"))
        else:
            msgs.append(_msg("error", "E_TAX_MISMATCH", f"totals[{rate}].tax",
                             f"{rate}% tax {st} does not match: expected {', '.join(f'{exp[r]} ({r})' for r in allowed)}.",
                             f"{rate}%の税額 {st} が合いません。正しくは {', '.join(f'{exp[r]}({r})' for r in allowed)}。"))
    for rate in totals:
        if rate not in sums:
            msgs.append(_msg("warning", "W_TOTAL_WITHOUT_ITEMS", "totals", f"Total row for {rate}% has no items.",
                             f"{rate}%の合計行がありますが、その税率の品目がありません。"))
    req["4_total_per_rate_with_rate"] = t_ok
    req["5_tax_per_rate"] = x_ok
    buyer = inv.get("buyer") or {}
    req["6_buyer_name"] = bool(str(buyer.get("name") or "").strip())
    if not req["6_buyer_name"]:
        msgs.append(_msg("error", "E_BUYER_MISSING", "buyer.name", "Buyer name is missing (required item 6).",
                         "交付を受ける事業者(買い手)の氏名・名称がありません(記載事項6)。"))
    errors = [m for m in msgs if m["level"] == "error"]
    warnings = [m for m in msgs if m["level"] == "warning"]
    return {"ok": not errors, "errors": errors, "warnings": warnings, "required_items": req,
            "registration_number": {k: rn[k] for k in ("normalized", "valid_format", "corporate_check_digit_ok")},
            "by_rate": by_rate, "price_basis": basis,
            "not_checked": ["whether the registration number is currently registered (needs the NTA site)",
                            "simplified invoices (適格簡易請求書) and other special cases"]}
