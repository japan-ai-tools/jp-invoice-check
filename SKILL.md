---
name: jp-invoice-check
description: Use when drafting, reviewing or accepting a Japanese invoice (請求書・適格請求書・インボイス), or when computing Japanese consumption tax (消費税) on an invoice. Checks the six required items of a qualified invoice, reduced-rate (8%) marks, totals per rate, and the rule that tax is rounded once per invoice per rate (not per line), with a script so the numbers are computed, not guessed.
---

# Japanese qualified invoice (適格請求書) check

Do not compute the tax in your head. Run the checker.

## When drafting an invoice

1. Put the invoice in this JSON shape (amounts are integers in yen):

```json
{"issuer": {"name": "...", "registration_number": "T + 13 digits"},
 "date": "2026-10-28",
 "buyer": {"name": "..."},
 "price_basis": "exclusive",
 "items": [{"description": "...", "amount": 1000, "rate": 10},
           {"description": "※ food", "amount": 500, "rate": 8, "reduced": true}],
 "totals": [{"rate": 10, "amount": 1000, "tax": 100}, {"rate": 8, "amount": 500, "tax": 40}]}
```

   - `price_basis`: `exclusive` if line amounts are before tax (税抜), `inclusive` if tax is included (税込).
   - `date` may be a period: `{"from": "2026-10-01", "to": "2026-10-31"}`.
2. For each rate, get the tax from the total of that rate, once:
   Use `python -c "from jpinvoice import expected_tax; print(expected_tax(TOTAL, RATE, 'exclusive', 'floor'))"`
   Rounding method (floor / half_up / ceil) is the issuer's choice. Use the one the issuer already uses; if unknown, floor.
3. Run `python -m jpinvoice invoice.json` (from the folder that contains `jpinvoice/`). Exit code 0 = no errors.
4. Fix every `error`. Show `warnings` to the user. Each message has English (`message`) and Japanese (`message_ja`).

## When reviewing an invoice someone sent

Extract it into the same JSON (keep the numbers exactly as printed, do not fix them while extracting), run the checker, and report the errors with the field names.

## Rules the checker applies

- Six required items: issuer name and registration number; transaction date; description, with reduced-rate items marked (e.g. ※); total per tax rate with the rate; consumption tax per rate; buyer name.
- Tax is rounded once per invoice per rate. Summing per-line rounded tax is the most common mistake (`E_TAX_LINE_ROUNDING`).
- Registration number: `T` + 13 digits. For a corporation the 13 digits are the corporate number, whose first digit is a check digit (`W_REGNO_CHECK_DIGIT` if it does not match).

## What it does not check

- Whether the number is registered today. Tell the user to confirm on the NTA publication site (適格請求書発行事業者公表サイト) if it matters.
- Simplified invoices (適格簡易請求書), return invoices (返還インボイス) and other special cases.
- Tax advice. For edge cases, the user should ask a tax accountant (税理士).

Source of the rules: National Tax Agency pamphlet https://www.nta.go.jp/publication/pamph/shohi/shohizei_11.pdf
