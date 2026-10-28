# JP Invoice Check

<!-- mcp-name: io.github.ayatrader/jp-invoice-check -->

**An Agent Skill, CLI and MCP server that checks a Japanese qualified invoice (適格請求書): the six required items, reduced-rate marks, totals per rate, and the rule that consumption tax is rounded once per invoice per rate.**

Japan's invoice system requires six items on every qualified invoice, and the tax must be rounded once per invoice for each rate, not line by line ([NTA pamphlet](https://www.nta.go.jp/publication/pamph/shohi/shohizei_11.pdf): 「端数処理は一請求書当たり、税率ごとに1回ずつ」). A language model that writes an invoice tends to compute tax per line and add it up. On the example below that gives 597 yen instead of 599 for the 10% items and 285 instead of 286 for the 8% items.

This package computes instead of guessing:

- checks the six items and returns each as true/false (`required_items`)
- recomputes tax per rate for floor, half-up and ceil rounding (the method is the issuer's choice) and tells you which one the invoice used
- names the per-line rounding mistake (`E_TAX_LINE_ROUNDING`) separately from other wrong tax
- flags 8% items without a reduced-rate mark, and reduced marks on 10% items
- normalizes the registration number (full-width, hyphens) and checks `T` + 13 digits and the corporate-number check digit
- reads ISO dates, `2026年10月28日`, `令和8年10月28日`, and periods

Every message comes in English and Japanese. Python standard library only. No network. MIT.

## Use as an Agent Skill

Copy this folder into your skills folder (for Claude: `~/.claude/skills/jp-invoice-check/`). `SKILL.md` tells the agent to put the invoice into JSON and run the checker instead of computing tax itself.

## CLI

```bash
python -m jpinvoice examples/ok.json                    # exit 0
python -m jpinvoice examples/line_rounding_mistake.json # exit 1, lists the errors
python -m jpinvoice invoice.json --rounding floor       # require one rounding method
```

```python
from jpinvoice import check_invoice, expected_tax
expected_tax(5997, 10)                      # 599  (exclusive, floor)
expected_tax(1000, 10, "inclusive")         # 90
check_invoice(invoice)["errors"]            # [{level, code, field, message, message_ja}, ...]
```

## MCP server (for AI agents)

```json
{"mcpServers": {"jp-invoice-check": {"command": "python", "args": ["-m", "jpinvoice.server"], "cwd": "/path/to/jp-invoice-check"}}}
```

| Tool | Input | Returns |
|---|---|---|
| `jp_invoice_check` | `invoice` (object) | `ok`, `errors`, `warnings`, `required_items`, `by_rate` |
| `jp_invoice_registration_number` | `number` | `normalized`, `valid_format`, `corporate_check_digit_ok`, `messages` |
| `jp_invoice_tax` | `amount`, `rate`, `price_basis`, `rounding` | `tax` |

All tools are read-only.

## Error codes

`E_REGNO_MISSING` `E_REGNO_FORMAT` `E_ISSUER_NAME_MISSING` `E_DATE_MISSING` `E_DATE_INVALID` `E_ITEMS_MISSING` `E_ITEM_DESCRIPTION_MISSING` `E_RATE_INVALID` `E_REDUCED_NOT_MARKED` `E_REDUCED_RATE_MISMATCH` `E_TOTAL_BY_RATE_MISSING` `E_TOTAL_MISMATCH` `E_TAX_MISSING` `E_TAX_LINE_ROUNDING` `E_TAX_MISMATCH` `E_BUYER_MISSING`; warnings `W_REGNO_NORMALIZED` `W_REGNO_CHECK_DIGIT` `W_TOTAL_WITHOUT_ITEMS`.

## Limits

- Offline. It does not check whether a registration number is registered today; use the NTA publication site for that.
- The check digit rule used is the corporate-number rule. For a sole proprietor's number a mismatch is reported only as a warning.
- Simplified invoices (適格簡易請求書), return invoices and other special cases are not covered.
- This is a checking tool, not tax advice.
- Tests: `python -m unittest discover -s tests` (18 tests)

---

## 日本語

適格請求書(インボイス)を検査する Agent Skill・コマンド・MCPサーバーです。

- 記載事項6つ(発行者名と登録番号・取引年月日・取引内容と軽減税率の印・税率ごとの合計と税率・税率ごとの消費税額・買い手の名称)を1つずつ判定
- 消費税は「1つの請求書につき税率ごとに1回」端数処理。品目ごとに端数処理して足す誤り(AIがよくする)を `E_TAX_LINE_ROUNDING` で知らせます。切捨て・四捨五入・切上げのどれを使ったかも返します
- 8%の品目に※などの印がない、10%なのに印がある、を検出
- 登録番号を整え(全角・ハイフン)、「T+13桁」と法人番号の検査用数字を確認
- 西暦・和暦(令和・平成)・期間の日付に対応

外部通信なし(登録が今も有効かは国税庁の公表サイトで確認してください)・Python標準ライブラリのみ・MIT。税務の助言ではありません。
