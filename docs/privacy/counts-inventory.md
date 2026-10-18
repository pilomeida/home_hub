# Which transactions count: inventory of every query site (decision D17)

`Transaction.counts()` (`app/models/transaction.py`) is the one definition: `settled_by_id IS NULL AND (review_status IS NULL OR review_status = 'confirmed')`. A line waiting for a check (`needs_check`) or rejected counts nowhere.

**Baseline (2026-10-17):** `counts()` also requires `Transaction.after_baseline()`: dated (paid, else due, else issue date) on or after 10 Oct 2025 (`app/baseline.py`). Older rows are kept but shown and counted nowhere. The screens that list rows (Transactions, Needs Review, bulk classify, loan history) and the classification queue add `after_baseline()` explicitly. Dedup, matching and bank-sync sites still see old rows on purpose. Tests: `tests/test_baseline.py`.

- **(a)** a figure, chart, budget, forecast, count or Ask answer a person reads as money: uses `Transaction.counts()`, and has a test in `tests/test_counts_sites.py` (the test name is in the last column).
- **(b)** processing, matching, display lookup or hygiene: a held row must stay visible to it. Marked in the code with `# keeps held rows: <why>` on the line above the query.

Line numbers are those of the commit that added this file.

## (a) Totals, displays and Ask: 11 sites

| file:line | purpose | (a)/(b) | reason / test |
|---|---|---|---|
| app/domains/financials/ask.py:39 | Ask tool `financials_spending`: spend total and groups | (a) | Ask must never read a held line. `test_ask_spending_total_counts_only_rows_that_count` |
| app/domains/financials/ask.py:64 | Ask tool `financials_find_transactions`: lists transactions with citations | (a) | same. `test_ask_find_transactions_lists_only_rows_that_count` |
| app/domains/financials/overview.py:18 | Overview card "Spent this month" | (a) | had NO settled filter before (see surprises). `test_financials_card_spent_this_month`, `test_overview_page_shows_the_right_figures` |
| app/services/overview_service.py:58 | income/expense per month: Income, Expenses, Net flow cards, cash-flow chart | (a) | `test_overview_income_expenses_and_net_flow`, `test_overview_page_shows_the_right_figures` |
| app/services/overview_service.py:137 | Cash card: running net balance of cash accounts | (a) | `test_overview_cash_balance` |
| app/services/overview_service.py:236 | Yearly commitments card: actual total | (a) | `test_overview_yearly_commitments_actual_total` |
| app/services/overview_service.py:299 | "Where it went" category comparison (also drives narrative and anomaly alerts) | (a) | `test_overview_where_it_went_category_comparison` |
| app/services/tag_service.py:136 | tag totals (month, year, last year, count) | (a) | `test_tag_totals_count_only_rows_that_count` |
| app/services/budget_service.py:174 | budget actuals per category node and month; feeds spend, forecast ("expected"), unsorted spend/received | (a) | `test_budget_line_spent_counts_only_rows_that_count`, `test_budget_unsorted_outflow_counts_only_rows_that_count` |
| app/services/budget_service.py:273 | loan instalments already paid in the period | (a) | `test_budget_loan_instalments_paid_counts_only_rows_that_count` |
| app/routers/loans.py:108 | "Insurance paid (linked debits)" total and last premium on the loan pages | (a) | had NO settled filter before. `test_loan_page_insurance_paid_counts_only_rows_that_count` |

`app/services/budget_forecast.py`, `overview_charts.py` and `loan_math.py` run no transaction query: they work on the figures from the sites above (or on statement snapshots), so they follow.

## (b) Leave as is, with a `# keeps held rows:` comment: 44 sites

| file:line | purpose | (a)/(b) | reason |
|---|---|---|---|
| app/routers/transactions.py:107 | Transactions page list | (b) | the ledger shows every row (settled ones already show with a marker) and adds up nothing; see open point 1 |
| app/routers/transactions.py:128 | counterpart lookup for transfer rows | (b) | display lookup |
| app/routers/transactions.py:159 | page count of the list above | (b) | same rows as the list |
| app/routers/transactions.py:198 | documents settled by a bank row (display) | (b) | display lookup; the settled filter here is the relation itself |
| app/routers/transactions.py:327 | bulk edit of ticked rows | (b) | acts on what the person ticked |
| app/routers/transactions.py:538 | link a merchant's rows to a new commitment | (b) | bookkeeping |
| app/routers/bills.py:68 | lines of one document on its page | (b) | lists what was read from that document; no total |
| app/domains/financials/handler.py:89 | re-file blocker check | (b) | must see every row of the document |
| app/domains/financials/handler.py:94 | re-file blocker check (linked rows) | (b) | same |
| app/domains/financials/handler.py:107 | document delete: removes its rows | (b) | held rows are deleted with their document |
| app/domains/financials/handler.py:121 | document delete: releases bills settled by its bank rows | (b) | a held bank row still settled them |
| app/services/classification_engine.py:348 | recurring-payment detection | (b) | classification |
| app/services/classification_engine.py:370 | debt-candidate detection | (b) | a person decides; shown whole |
| app/services/classification_engine.py:398 | `unsorted_transactions_query` (Needs Review "unsorted") | (b) | a held line is still classified so it is filed right once confirmed; the existing settled filter is kept untouched |
| app/services/classification_engine.py:414 | Needs Review "unclassified" (no merchant) | (b) | same |
| app/services/classification_engine.py:490 | entry counts that order the merchant list | (b) | sort order only |
| app/services/overview_service.py:421 | "N items waiting in Needs Review" attention item | (b) | counts classification work, not money; held lines have their own To-check count |
| app/services/merchant_assign.py:42 | assign a category to a merchant's history | (b) | moves rows |
| app/services/merchant_assign.py:96 | bulk-classify page: entries and total per merchant | (b) | sizing aid over the very rows that will be filed (settled rows are in it today); not a spending figure; see open point 2 |
| app/services/merchant_assign.py:126 | bulk-classify page: entries filed elsewhere | (b) | same |
| app/services/provider_rules.py:85 | bulk-classify by provider text: groups and totals | (b) | same rows as `apply_to_providers` files; see open point 2 |
| app/services/taxonomy.py:138 | "has this merchant ever been paid?" (refund rule) | (b) | classification rule, not a total |
| app/services/taxonomy_refile.py:37 | migration refile of every row | (b) | moves rows |
| app/services/taxonomy_refile.py:130 | reclassify unsorted rows | (b) | moves rows |
| app/services/taxonomy_migration.py:86 | raw SQL: can a category node be dropped? | (b) | any row, held or not, pins it |
| app/services/merchant_categories.py:40 | apply a merchant's category to its history | (b) | moves rows |
| app/services/merchant_reconcile.py:61 | merchant/category consistency pass | (b) | moves rows |
| app/services/merchant_relabel.py:48 | relabel merchants | (b) | all rows |
| app/services/merchant_relabel.py:75 | is a merchant still used? | (b) | any row |
| app/services/merchant_merge.py:67 | raw SQL: rows to move on merchant merge | (b) | moves rows |
| app/services/transfer_direction.py:39 | repair transfer direction | (b) | moves rows |
| app/services/dedup.py:27 | bill re-upload duplicate check | (b) | dedup |
| app/services/document_reconcile.py:72 | match bills to bank rows | (b) | matching; see open point 3 |
| app/services/debt_ledger.py:235 | backfill ledger lines from existing debt links | (b) | person-made links; see open point 4 |
| app/services/loan_linking.py:66 | auto-link instalments to a loan | (b) | matching |
| app/services/loan_linking.py:81 | auto-link insurance debits to a loan | (b) | matching |
| app/services/bankapi/sync.py:82 | sync window start (statement rows) | (b) | where the stored rows end |
| app/services/bankapi/sync.py:91 | sync window start (any row) | (b) | same |
| app/services/bankapi/sync.py:139 | renumbered-booking twin check | (b) | dedup |
| app/services/bankapi/sync.py:155 | match API rows to statement rows | (b) | a held statement row must still absorb its API twin |
| app/services/bankapi/sync.py:241 | already-synced check | (b) | dedup |
| app/services/bankapi/sync.py:293 | recovery after a failed classify | (b) | dedup |
| app/services/bankapi/topups.py:40 | pair Revolut top-ups with Santander debits (debits) | (b) | matching |
| app/services/bankapi/topups.py:50 | same (credits) | (b) | matching |

Existing hand-written settled checks that stay, inside (b) code: `classification_engine.py` (the two queue queries above), `document_reconcile.py` (candidates and `used`), `merchant_categories.py` (skip settled rows), `transactions.py` (the "settled" row marker), `handler.py` (release on delete). Single-row `session.get(Transaction, id)` calls (reach.py, handler.py, transactions.py actions) decide nothing about totals and are not listed.

## Open points for the coordinator

1. **Transactions page** shows held and rejected rows like any other row, with no marker, and the Overview cards link into it (so a card's total can be smaller than the rows behind its link). D11 says a waiting value is "marked pending check". A small marker in `templates/transactions/_rows.html` (like the existing "settled by bank entry" tag) would close this; not done, since it is display work for the Step 4 screens.
2. **Bulk-classify totals** (merchant list and provider groups) include every entry, as they already include settled rows. They are a sizing aid and must match the rows that filing will move, so they were left as (b). If Pedro reads those totals as spending, change them together with the filing queries.
3. **Reconciliation** can settle a bill with a held or rejected bank row. While that row is held, neither the bill nor the bank row counts; if the person rejects the bank row, the bill's payment disappears from every total. Consider excluding `rejected` bank rows from the match pool.
4. **Debt ledger / informal loans**: a held transaction a person links to an informal debt creates a `DebtEntry` that counts in the debt balance immediately. The balance is read from `DebtEntry`, not from a transaction query, so `counts()` cannot reach it. Consider refusing to link a held row.
