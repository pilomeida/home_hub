# Loans & Savings — Round 2 (Pedro's feedback before rollout)

> Addendum to `2026-10-06-loans-savings.md` (built, reviewed, uncommitted). Same rules: no git commit/push by implementers, TDD, no subagents inside implementers, SYNTHETIC data only (no real loan numbers, amounts, names — the repo is grep-checked for them), never touch the real DB, focused tests only (controller runs the full suite).

## Pedro's decisions (binding)
1. Upload page: statements and loan printouts are **independent and optional** — he may upload one type, the other, or both (at least one file).
2. **Data registered from a trustworthy source is never discarded**: deleting/re-filing/withdrawing a source document must NOT delete loan/savings rows derived from it.
3. **Loan insurance debits** (`SEG VIDA …`, `SEG:EDF …`, `SEG:LAR …`) must be tied to their loans.
4. **Informal person-to-person loans matter**: repayments may be partial; add-on loans (further advances) may come in; the balance must be exact and explainable.
5. **Interest rate**: EURIBOR-indexed; revised every 3/6/12 months; the statement shows the applied rate (TAN = indexante + spread). **The spread is constant**: any change of spread is a RED FLAG.
6. **Interest-only instalments are NOT normal** (the bank once charged only interest for 15 months by error): flag them in red whenever they happen — including the historical ones when they are uploaded.

## Global constraints
- Nothing is silently dropped: anomalies are stored (the data stays) AND flagged.
- Alerts are acknowledgeable (with an optional note) and stay visible until acknowledged: on the Loans & Savings page (red) and as Overview "Needs attention" items.
- Colours: red = anomaly (muted red as elsewhere), light + dark; no viewport tag / global CSS.

---

## Block A — Rate, spread and red flags (items 5, 6)

**Model (edit the unreleased migration `c3a9d4e7f215` in place; re-verify up/down on a scratch DB copy):**
- `LoanSnapshot` gains `indexante_percent: Optional[float]`, `spread_percent: Optional[float]` (from the "Próxima Prestação" block: Indexante, Spread; TAN is already `next_rate_percent`).
- `Debt` gains `spread_percent: Optional[float]` = the **baseline spread** (first spread ever seen; never silently updated).
- New table `loan_alerts`: `id, debt_id FK, kind, ref (str, e.g. "inst:16" or "snap:2026-09-30"), message, detected_on: date, document_id FK nullable, acknowledged: bool=False, ack_note: Optional[str], acknowledged_at: Optional[datetime]`; `UniqueConstraint(debt_id, kind, ref)`.
- Alert kinds: `interest_only` (an instalment with capital == 0 and interest > 0), `no_capital_no_interest` is NOT an alert (insurance-only rows are ignored as before), `spread_changed` (snapshot spread differs from `Debt.spread_percent` by more than 0.001), `rate_inconsistent` (`|TAN − (indexante + spread)| > 0.005` when all three are present).

**Extraction:** `ExtractedLoan` gains `next_indexante_percent`, `next_spread_percent` (prompt: from the Próxima Prestação block; null if absent; nullable in the schema; parser `.get`).

**Logic (new module `app/services/loan_alerts.py`):**
- `detect_alerts_for_instalments(session, debt, instalments, document) -> int` — creates `interest_only` alerts (idempotent by unique key; ref `inst:<number>`; message e.g. "Instalment 7: interest only, no capital repaid (€X interest)").
- `detect_alerts_for_snapshot(session, debt, snapshot) -> int` — baseline spread: if `Debt.spread_percent is None` set it from the first snapshot that has a spread; else compare; create `spread_changed` / `rate_inconsistent` alerts (ref `snap:<as_of>`).
- Called from `apply_positions` (snapshots + instalments of loan rows) and `apply_loan_history` (instalments), inside the same transaction; failures in alert detection must not fail the document (log, rollback only the alert step, continue) but must be covered by a test.
- `acknowledge_alert(session, alert_id, note) -> LoanAlert`.
- **`reconcile_loan` stays as is**; interest-only is NOT a reconciliation failure (the data is stored) but IS an alert. Update docstrings/tests/plan wording that call interest-only "normal".

**UI:**
- Loans page: per loan, a red "Red flags" block listing unacknowledged alerts (message, detected date, an Acknowledge form with optional note → `POST /financials/loans/alerts/{id}/ack`), and acknowledged ones collapsed. A red count badge on the page header when any unacknowledged alert exists.
- Overview Needs-attention: one item per unacknowledged alert (red), linking to the loan's page.
- Loan detail page: a "Rate history" table from snapshots: as_of, indexante, spread, TAN (effective), and highlights a spread change in red.

**Tests (synthetic):** interest-only series (instalments 1–3 capital 0) → 3 alerts, applying the same history again adds none; spread unchanged across two snapshots → no alert; spread +0.1 → `spread_changed` alert; TAN ≠ indexante+spread → `rate_inconsistent`; baseline set from the first snapshot only; acknowledge with note; Overview shows an item per unacknowledged alert and none after acknowledging; Loans page shows the red block; extraction parses indexante/spread; strictness schema accepts nulls for them.

## Block B — Never discard registered data (item 2) and flexible upload (item 1)

- Make `document_id` **nullable** on `loan_movements`, `loan_snapshots`, `savings_snapshots`, `balance_snapshots`, `loan_alerts` (edit the unreleased migration + models; `PositionExtraction` keeps its document FK but is allowed to be deleted with the document).
- `FinancialsHandler.withdraw` for `statement_positions` / `loan_history` / `statement` documents: **detach** (set `document_id = NULL` on every position row from that document) and delete only the `PositionExtraction` row; **never delete** position rows or Debts. Remove `replay_positions` and its calls (no longer needed), keep `_recompute_debt` if still used. A re-upload of the same file after a withdraw re-applies idempotently (upsert) and re-attaches `document_id` where it is NULL.
- Document deletion elsewhere (any code path that deletes a `Document` row) must not fail because of these FKs — nullable FK + detach covers it; grep for `session.delete(document` and check.
- Tests: withdrawing the only document of a loan leaves the loan, its movements, snapshots and balance intact (document_id NULL); re-uploading re-attaches; the old "withdraw deletes rows" tests are rewritten to the new rule.
- Upload page/route: both file inputs are optional; the form must submit with one empty; the route returns 400 only when BOTH are empty ("choose at least one file"). Update the page text: "Upload statements, loan printouts, or both". Tests for statements only, printouts only, both, neither.

## Block C — Loan insurance debits tied to loans (item 3)

- `LoanMovement` gains `insurance_life` and `insurance_building` columns (floats, default 0.0); `insurance` stays as the stored total (= life + building); `aggregate_components` / `Instalment` carry both parts; `apply_*` and the merge/upsert rule fill them like `insurance` (fill if empty, never overwrite).
- Taxonomy seed (Plan 1 `taxonomy_seed.py`): under the existing out group `Loans & debt` add category `Loan insurance` (cadence monthly, legacy `L.INSURANCE`) with sub-categories `Life insurance (loan)` and `Building insurance (loan)` → slugs `loans-debt.loan-insurance.life-insurance-loan` and `loans-debt.loan-insurance.building-insurance-loan` (verify what `_slug` produces and assert on the real slugs). These nodes are monthly, budgetable, and count as ordinary spend in Plan 2's budget view (they are NOT instalments).
- New table `loan_insurance_rules`: `id, debt_id FK, component ("life"|"building"), normalized_key (unique)`.
- `app/services/loan_insurance.py`: `insurance_key(provider_text)` — lowercased provider with trailing date fragments removed (patterns like `2026-09-02/2026-10-01`, `-2026/08/21`, `2026/08/21`, standalone ISO dates) and whitespace collapsed; keys look like `seg edf`, `seg lar`, `seg vida 15.123456` (`:` is normalised to a space); only for providers starting with `seg` (case-insensitive) followed by `:` or a space, and only the families `seg vida` (life) / `seg edf` and `seg lar` (building) are ever linked or get rules. `link_insurance_transaction(session, txn) -> bool`: (1) rule by key → link; (2) else amount match: a DEBIT txn whose amount equals (±0.01) the `insurance_life` or `insurance_building` of exactly ONE loan's movement with `movement_date` within ±7 days of the txn's paid_date → link and CREATE the rule (component from the matching column); ambiguity (several loans/components) → no link, no rule. Linking sets `txn.debt_id`, files under the matching insurance node via `file_transaction`, never overwrites an existing `debt_id`, idempotent.
- `link_all_unlinked` also runs insurance linking for all unlinked `seg…` debits (load movements/rules once); `classify_transaction` calls `link_insurance_transaction` after the instalment link attempt (loan-number link first). Wire it into the same places as Task 4's wiring (process_positions_document, process_loan_history_document, assign_history_to_loan) and keep failures non-fatal.
- Loans page: per loan show "insurance paid (linked debits)" total and the last premium, next to the instalment line; Overview unchanged.
- Tests (synthetic): amount-match links a `SEG:EDF 2026-09-02/2026-10-01` debit of 20.00 to the loan whose movement has building insurance 20.00 in that month and creates the rule; a later debit of 21.50 (premium changed) links through the rule; `SEG VIDA 15.000001-2026/08/21` keys to `seg vida 15.000001`; two loans with the same insurance amount → ambiguous, not linked; non-`seg` providers untouched; credits untouched; history back-link via `link_all_unlinked`; filing node + legacy category agree; budget view counts them as spend and not as `loan_instalments`.

## Block D — Informal loans done properly (item 4)

Current weakness: balance = `original_amount` ± linked transactions with a heuristic that skips the creating transaction. Replace with an explicit ledger.

- **Roles:** every linked transaction on an informal debt becomes a `DebtEntry` with an explicit kind. Default role from the money direction and the debt's direction: for `OWED_TO_US` (Pedro lent): DEBIT = **advance** (balance up), CREDIT = **repayment** (balance down); for `OWED_BY_US` (Pedro borrowed): CREDIT = advance, DEBIT = repayment. A TRANSFER-type transaction has no reliable direction: the link form must ask (default: advance for `OWED_TO_US`, repayment for `OWED_BY_US`); the user can always override advance↔repayment at link time. Table `debt_entries`: `id, debt_id FK, kind ("advance"|"repayment"|"adjust_up"|"adjust_down"), amount: float (positive), entry_date: date, transaction_id FK nullable UNIQUE, note: Optional[str], created_at`.
- **Balance** = Σ advances + Σ adjust_up − Σ repayments − Σ adjust_down. `Debt.current_balance` is recomputed after every entry change; never negative displayed (an overpayment is shown as "overpaid by €X" and stored as a negative internal sum). `original_amount` = Σ advances (recomputed).
- `link_debt` (Needs Review "Possible debt transfers" + the existing route): linking a transaction creates/uses the Debt and **creates a `DebtEntry` with the right kind** (role derived as above; the form shows the derived role and lets the user override advance↔repayment); a new debt created from a transaction starts with that transaction as its first advance (no `original_amount` heuristic anymore). Linking more transactions to an existing debt adds entries; partial repayments simply reduce the balance; add-on loans are further advances. The `DebtMatchRule` auto-link creates entries with the role derived from direction automatically.
- **Manual entries** (cash lent, history before the bank data, corrections): Loans page, informal section: per debt a ledger table (date, kind, amount, linked transaction link or note, running balance) and a small form "Add entry" (kind advance/repayment/adjustment up/down, amount, date, note) → `POST /financials/loans/debts/{id}/entries`; delete a manual entry (only entries without a transaction) → `POST …/entries/{entry_id}/delete`. Also a form to create an informal loan with no transaction ("New informal loan": person name, direction, opening amount, date, note → first advance as a manual entry).
- Filing of linked transactions (only when `direction_matches` the node, per Plan 1's rule; otherwise leave the filing as is): advance on a lent debt → `loans-debt.money-lent-out.loan-to-friends`; repayment received on a lent debt → `loans-debt-in.repayments-received.from-friends`; advance on a borrowed debt → `loans-debt-in.money-borrowed.personal-loan-received`; repayment of a borrowed debt → `loans-debt.loan-repayments.personal-loans`. (Verify the real slugs in the seed. A later UI choice between family/friends is out of scope; the Loans page shows the person anyway.)
- Loans page informal section: per loan: person, direction, advances total, repayments total, **balance**, last activity; totals feed `position_totals` (owed by us / owed to us) exactly as before. Overview debt KPI unchanged in meaning.
- Migration: edit `c3a9d4e7f215` in place to add `debt_entries`; a backfill step in the migration or a service function `rebuild_entries_from_links(session)` creates `DebtEntry` rows from existing informal links (transactions with `debt_id` on informal debts) using the old rule for roles; run it once from the migration on SQLite (idempotent via the unique `transaction_id`).
- Remove the old `informal_balance` heuristic; keep the function name but compute from entries.
- Tests (synthetic): lend 1000 (advance) then 300 partial repayment then an add-on advance 500 → balance 1200; borrowed direction mirrored; manual cash advance + adjustment; delete a manual entry; overpayment displays "overpaid"; role override at link time; auto-link via `DebtMatchRule` creates the right entry; totals in `position_totals`; filing nodes per case; rebuild from legacy links is idempotent.

## Out of scope
Telegram/other channels; Euribor forecasting (only history + flags); splitting transactions.
