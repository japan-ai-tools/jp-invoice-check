# JP Invoice Check

<!-- mcp-name: io.github.japan-ai-tools/jp-invoice-check -->

**An Agent Skill, CLI and MCP server that checks a Japanese qualified invoice (適格請求書): the six required items, reduced-rate marks, totals per rate, and the rule that consumption tax is rounded once per invoice per rate.**

Japan's invoice system requires six items on every qualified invoice, and the tax must be rounded once per invoice for each rate, not line by line ([NTA pamphlet](https://www.nta.go.jp/publication/pamph/shohi/shohizei_11.pdf): 「端数処理は一請求書当たり、税率ごとに1回ずつ」). A language model that writes an invoice tends to compute tax per line and add it up. On the example below that gives 597 yen instead of 599 for the 10% items and 285 instead of 286 for the 8% items.

This package computes instead of guessing:

- checks the six items and returns each as true/false (`required_items`)
- recomputes tax per rate for floor, half-up and ceil rounding (the method is the issuer's choice) and tells you which one the invoice used
- names the per-line rounding mistake (`E_TAX_LINE_ROUNDING`) separately from other wrong tax
- flags 8% items without a reduced-rate mark, and reduced marks on 10% items
- normalizes the registration number (full-width, hyphens) and checks `T` + 13 digits and the corporate-number check digit
- reads ISO dates, `2026年10月28日`, `令和8年10月28日`, and periods
- **new in 0.2.0:** compares the invoice with your company's own written rule (one rounding method, tax-exclusive or inclusive) and says how many yen a supplier's invoice differs and why
- **new in 0.2.0:** the rule is fixed when a person starts the checker; the AI being checked cannot change it, and every result carries a fingerprint of the rules used

Every message comes in English and Japanese. Python standard library only. No network. MIT.

## Your company rule (0.2.0)

Decide one rounding method for your company and write it down once:

```json
{"name": "Example Co. invoice rules", "rounding": "floor", "price_basis": "exclusive"}
```

Then every check also compares the invoice with that rule (`policy_check`):

- `role: "issue"` (an invoice you send): not following your rule is an error (`E_POLICY_ROUNDING`, `E_POLICY_PRICE_BASIS`).
- `role: "receive"` (an invoice a supplier sent): their method may legally differ, so it is a warning (`W_POLICY_ROUNDING`) that names the method they used and the difference in yen. One check replaces the back-and-forth of finding why the numbers do not match.

## Keep the checker out of the AI's reach

A check is only worth something if the AI being checked cannot rewrite it. (Thanks to a reader's comment on Qiita for this point.)

1. **Install it outside the agent's working folder** and run it as a separate process, for example `pip install "git+https://github.com/japan-ai-tools/jp-invoice-check"` into an environment the agent does not write to (PyPI may still have 0.1.0, which has no `--policy`; run `python -m jpinvoice --fingerprint` and make sure it says 0.2.0 or later). Do not copy the source into the project the agent edits.
2. **Load the rule when you start it**, not from the AI: `jp-invoice-check --policy /etc/company/invoice_policy.json` or set `JPINVOICE_POLICY`. Make that file read-only and keep it outside the agent's folder. No tool argument can change it, and a `rounding` field inside the invoice is ignored while a rule is loaded (`W_INVOICE_ROUNDING_IGNORED`).
3. **Deny edits** to the install path and the policy file in your agent's permissions (in Claude Code, a `deny` rule for `Edit` and `Write` on those paths).
4. **Check the fingerprint.** Each result has `checker.code_sha256`. For 0.2.0 it is:

   `cd3e992f709f045f2047e8f4283e49ad3f825c2f752a5f984e29a66036eb32fe`

   If it differs, the checking code was changed after installation. `python -m jpinvoice --fingerprint` prints it.

## Use as an Agent Skill

`SKILL.md` tells the agent to put the invoice into JSON and call the checker instead of computing tax itself. Prefer the MCP server (installed as above) and let the skill only call it.

## CLI

```bash
python -m jpinvoice examples/ok.json                    # exit 0
python -m jpinvoice examples/line_rounding_mistake.json # exit 1, lists the errors
python -m jpinvoice invoice.json --policy examples/company_policy.json                 # an invoice you send
python -m jpinvoice supplier.json --policy examples/company_policy.json --role receive # one you received
python -m jpinvoice --fingerprint --policy examples/company_policy.json                # which rules are in use
```

```python
from jpinvoice import check_invoice, expected_tax
expected_tax(5997, 10)                      # 599  (exclusive, floor)
expected_tax(1000, 10, "inclusive")         # 90
check_invoice(invoice)["errors"]            # [{level, code, field, message, message_ja}, ...]
```

## Many invoices from a CSV (for accounting staff / 経理の人向け)

Export the invoice lines from Excel or your accounting software as CSV (one row per line item, UTF-8 or Shift_JIS),
then:

```
python -m jpinvoice.batch examples/invoices_sample.csv --html report.html [--policy company_policy.json] [--role receive]
```

You get one HTML page: which invoices are fine, which have errors (for example `E_TAX_LINE_ROUNDING`), and why,
in Japanese. Headers can be Japanese (請求書番号・発行者・登録番号・取引日・買い手・税込税抜・品名・金額・税率・軽減・
10%対象額・10%税額・8%対象額・8%税額) or English. See `examples/invoices_sample.csv`. Nothing leaves your PC.

Excel や会計ソフトから請求書の明細を CSV(1行=1品目)で書き出し、上のコマンドで何十件でもまとめて検査できます。
結果は1枚の HTML(誤りの理由つき・日本語)。外部への通信はありません。

### Without Python: one HTML file / Python なしで使う

Open `offline/jp_invoice_check.html` in a browser (double-click), choose the CSV, read the result. The same rules as the
Python checker; a test runs both on the same CSVs (sample, Shift_JIS and 500 generated invoices with typical mistakes)
and checks that they give the same verdicts and error codes (`tests/test_offline_html.py`, needs Node.js).
The page has a Content-Security-Policy of `default-src 'none'`, so it cannot send your file anywhere.

`offline/jp_invoice_check.html` をダブルクリックしてブラウザで開き、CSV を選ぶだけ。インストール不要。社内規則(端数処理・税抜/税込)と
「自社が出す/取引先から受け取る」を選べます。結果は HTML で保存でき、CSV のひな形も保存できます。ページは通信できない設定です。

## MCP server (for AI agents)

```json
{"mcpServers": {"jp-invoice-check": {"command": "jp-invoice-check", "args": ["--policy", "/etc/company/invoice_policy.json"]}}}
```

| Tool | Input | Returns |
|---|---|---|
| `jp_invoice_check` | `invoice` (object), `role` (`issue`/`receive`) | `ok`, `errors`, `warnings`, `required_items`, `by_rate`, `policy_check`, `checker` |
| `jp_invoice_checker_info` | (none) | `version`, `code_sha256`, `policy`, `policy_sha256` |
| `jp_invoice_registration_number` | `number` | `normalized`, `valid_format`, `corporate_check_digit_ok`, `messages` |
| `jp_invoice_tax` | `amount`, `rate`, `price_basis`, `rounding` | `tax` |

All tools are read-only.

### Over HTTP (remote MCP)

For agents that only connect to remote MCP servers (streamable HTTP). Standard library only, JSON responses, no sessions:

```
python -m jpinvoice.http_server --port 8765 [--policy company_policy.json]   # http://127.0.0.1:8765/mcp
```

It binds to 127.0.0.1 unless you pass `--host`. Bodies over 1 MB are refused. `GET /` answers `ok` for health checks. If you expose it, put it behind your own authentication. (New in the GitHub source; the PyPI package gets it in the next release.)

## How this differs from other free invoice checkers

We looked at the other free Japanese invoice MCP servers listed on Glama (checked 2026-11-03). Their descriptions cover registration-number checks, required items and tax calculation with one default rounding method. None of the descriptions we read says that it names the per-line rounding mistake. This package is built around that one rule:

- it recomputes tax per rate with floor, half-up and ceil, and tells you which method the invoice used, instead of assuming one
- it returns `E_TAX_LINE_ROUNDING` when the difference comes from rounding each line, separately from other wrong tax
- it compares a received invoice with your own written rounding rule (`E_POLICY_ROUNDING`) and says how many yen differ

It does not look up whether a registration number is registered today (see Limits).

## Error codes

`E_REGNO_MISSING` `E_REGNO_FORMAT` `E_ISSUER_NAME_MISSING` `E_DATE_MISSING` `E_DATE_INVALID` `E_ITEMS_MISSING` `E_ITEM_DESCRIPTION_MISSING` `E_RATE_INVALID` `E_REDUCED_NOT_MARKED` `E_REDUCED_RATE_MISMATCH` `E_TOTAL_BY_RATE_MISSING` `E_TOTAL_MISMATCH` `E_TAX_MISSING` `E_TAX_LINE_ROUNDING` `E_TAX_MISMATCH` `E_BUYER_MISSING` `E_POLICY_ROUNDING` `E_POLICY_PRICE_BASIS`; warnings `W_REGNO_NORMALIZED` `W_REGNO_CHECK_DIGIT` `W_TOTAL_WITHOUT_ITEMS` `W_POLICY_ROUNDING` `W_POLICY_PRICE_BASIS` `W_INVOICE_ROUNDING_IGNORED`.

## Limits

- Offline. It does not check whether a registration number is registered today; use the NTA publication site for that.
- The check digit rule used is the corporate-number rule. For a sole proprietor's number a mismatch is reported only as a warning.
- Simplified invoices (適格簡易請求書), return invoices and other special cases are not covered.
- This is a checking tool, not tax advice.
- Tests: `python -m unittest discover -s tests` (26 tests)

---

## 日本語

適格請求書(インボイス)を検査する Agent Skill・コマンド・MCPサーバーです。

**ほかの無料の検査道具との違い**(Glama に載っている日本のインボイス用MCPの説明を 2026-11-03 に読んだ範囲): 登録番号・記載事項・税額の計算(端数処理は1つの方式が既定)を扱うものはありますが、「品目ごとに端数処理した誤り」を名前で返すと書いたものは見つかりませんでした。この道具は、切捨て・四捨五入・切上げのどれで計算されたかを当て、品目ごとの丸めの誤りを `E_TAX_LINE_ROUNDING` で、自社の端数処理の決まりとのずれを `E_POLICY_ROUNDING` で返します。登録番号が今も登録されているかの照会はしません。

- 記載事項6つ(発行者名と登録番号・取引年月日・取引内容と軽減税率の印・税率ごとの合計と税率・税率ごとの消費税額・買い手の名称)を1つずつ判定
- 消費税は「1つの請求書につき税率ごとに1回」端数処理。品目ごとに端数処理して足す誤り(AIがよくする)を `E_TAX_LINE_ROUNDING` で知らせます。切捨て・四捨五入・切上げのどれを使ったかも返します
- 8%の品目に※などの印がない、10%なのに印がある、を検出
- 登録番号を整え(全角・ハイフン)、「T+13桁」と法人番号の検査用数字を確認
- 西暦・和暦(令和・平成)・期間の日付に対応

- **0.2.0 から:** 自社の端数処理の規則(切捨て・四捨五入・切上げのどれか1つと、税抜か税込か)を1つのファイルに書いておくと、請求書と見比べます。自社が出す請求書(`issue`)で規則と違えば誤り、取引先からの請求書(`receive`)なら「相手は四捨五入なので、自社の規則より+1円」のように、ずれの理由と金額を知らせます
- **0.2.0 から:** 規則は人が起動するときにだけ読み込みます(`--policy` か `JPINVOICE_POLICY`)。検査されるAIの側からは変えられません。結果には検査に使った規則の指紋(sha256)がつくので、検査の中身が書き換えられていないかを確かめられます。検査の道具は、AIが書き換えられない場所に入れて使ってください(上の「Keep the checker out of the AI's reach」)

外部通信なし(登録が今も有効かは国税庁の公表サイトで確認してください)・Python標準ライブラリのみ・MIT。税務の助言ではありません。
