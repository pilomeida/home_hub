# Financial OS — Data Model Foundation Design Spec

**Date:** 2026-08-21
**Status:** Approved for planning
**Scope:** Sub-project 1 of the Financial OS decomposition (`2026-08-21-financial-os-requirements-v2.md`). New entities and `Transaction` extensions everything else in the Financial OS depends on. Schema + migrations only — no data backfill/classification.

## Context & Goals

The current schema (`app/models/transaction.py`) is a flat `Category` enum with no `Account`, `Commitment`, `Debt`, or `Person` concept — `provider` is an unnormalized free-text string, and `OTHER_EXPENSE` is a catch-all for anything unusual (real examples in the existing 3,726 transactions: a €120,000 "CHEQUE COMPENSAÇÃO," an €11,450 "MAFRA AUTO 3," a €5,000 SEPA transfer to a named individual that is really an informal loan). The Financial OS v2 spec's Overview, Commitments/Planning, and Analysis screens all need entities this schema doesn't have yet.

Goal: add the minimum set of new entities and `Transaction` columns needed to represent accounts, recurring/yearly commitments, and formal/informal debt — grounded in the real data above, not designed in the abstract — without backfilling the existing 3,726 transactions into them (that's sub-project 3's classification engine, or a later manual pass) and without touching the classification engine, UI, or any other sub-project's concerns.

## Data Model

### Account

New table, one row per real bank/wallet account (today: one fully-ingested current account, plus a Revolut account known to exist but not yet fully ingested — see Pendings).

- `id`
- `name`: `str`
- `institution`: `str`
- `currency`: `str`
- `account_type`: enum `CHECKING` / `SAVINGS` / `CARD` / `WALLET`
- `identifier`: `Optional[str]` — IBAN or last-4, for human identification only, not parsed/validated

### Commitment

New table. Represents both recurring (monthly/quarterly) and yearly-cadence planned spend — the "IMI/vacation vs. weekend-break" distinction from the v2 spec is enforced simply by whether a Commitment row exists at all for a given kind of spend, not by a separate flag.

- `id`
- `name`: `str`
- `category`: `Category` (reuses the existing enum)
- `cadence`: enum `MONTHLY` / `QUARTERLY` / `YEARLY` / `IRREGULAR`
- `planned_amount`: `float`
- `year`: `Optional[int]` — populated only for `YEARLY` cadence. Each year is its own row (e.g. "IMI 2026," "IMI 2027" are two separate Commitment records with independent `planned_amount`s) — no evergreen/template row, no reset logic. `MONTHLY`/`QUARTERLY` commitments (Netflix, mortgage) are evergreen single rows and leave this `None`.
- `next_due_date`: `Optional[date]`

A yearly commitment paid in installments (e.g. IMI in 3-4 payments across the year) needs no extra schema: every installment transaction links to the same Commitment row via `Transaction.commitment_id` (below), and "actual so far" is the sum of linked transactions against that row's `planned_amount`. Tracking the *expected* installment dates themselves (a payment calendar) is out of scope here — see Pendings.

### Debt

New table. Two kinds sharing one shape, distinguished by whether a schedule exists:

- `id`
- `kind`: enum `FORMAL` / `INFORMAL`
- `person_id`: `Optional[int]` FK to `Person` — set only for `INFORMAL`
- `direction`: `Optional[str]` enum `OWED_TO_US` / `OWED_BY_US` — set only for `INFORMAL`
- `original_amount`: `float`
- `current_balance`: `float`
- `interest_rate`: `Optional[float]`
- `commitment_id`: `Optional[int]` FK to `Commitment` — set only when the debt has a recurring payment schedule (e.g. the mortgage links to its monthly-cadence Commitment). Ad-hoc informal debt (a loan from/to an individual with no fixed schedule) leaves this `None`; repayments/draws link directly via `Transaction.debt_id` instead — two distinct linkage paths for two genuinely different repayment patterns, not one mechanism forced to cover both.

### Person

New table, lightweight counterparty record for informal debt (so multiple loans to/from the same person roll up together).

- `id`
- `name`: `str`
- `notes`: `Optional[str]`

### Transaction additions

Four new nullable columns on the existing `transactions` table (nullable since this sub-project does no backfill — every existing row leaves these unset):

- `account_id`: `Optional[int]` FK to `Account`
- `commitment_id`: `Optional[int]` FK to `Commitment`
- `debt_id`: `Optional[int]` FK to `Debt` — independent of `commitment_id`; a transaction may satisfy a Commitment, link directly to a Debt, both, or neither
- `nature`: `Optional[Nature]` enum (see below)

### Nature (new enum, orthogonal to Category)

- `ESSENTIAL`
- `DISCRETIONARY`

Not a replacement for `Category` — a second, independent dimension. A transaction keeps its existing `Category` (e.g. `RESTAURANTS`) and separately gets a `Nature` (e.g. `DISCRETIONARY`). Nullable on `Transaction`, same reasoning as above (no backfill).

## Reporting Periods

No new table. Calendar month is the period unit; calendar year is the year a `YEARLY` Commitment's `year` field refers to. Rolling windows (1/3/6/12/18/24 months, used by the Overview KPI cards) and range presets (1M/3M/6M/9M/12M/YTD, used by the cash-flow chart) are computed at query time from `Transaction`/`Commitment` dates via shared query-helper functions — not persisted, since dates already express everything a Period table would.

## Migrations

Additive only: 4 new tables (`accounts`, `commitments`, `debts`, `people`), 4 new nullable columns on `transactions`. No column removals, no data migration, no changes to existing enums' existing values (`Category` and `TransactionType` are untouched; `Nature` is new and separate).

## Out of Scope

- Backfilling/classifying any of the existing 3,726 transactions into the new entities (`account_id`, `commitment_id`, `debt_id`, `nature` all stay `NULL` on existing rows after this sub-project ships).
- The classification engine itself (merchant/provider normalization, auto-tagging rules, yearly-vs-recurring auto-detection) — sub-project 3.
- Any UI (Overview, Commitments calendar, Debt view, etc.) — sub-projects 2, 4, 5.
- An expected-installment-date/payment-calendar mechanism for multi-installment yearly commitments — sub-project 4 (Commitments & Planning).
- Actually creating any `Account`, `Commitment`, `Debt`, or `Person` rows for the user's real accounts/commitments/debts — this spec is schema only; populating real rows (e.g. the mortgage, the Revolut account, Eduardo's loan) is a data-entry task for whoever builds against this schema next, not part of this sub-project.

## Pendings surfaced during this brainstorm (not resolved here — routing notes for later sub-projects)

- **Installment payment calendar** — IMI-style yearly commitments paid in 3-4 installments are representable now (multiple transactions linking to one Commitment row), but there's no way yet to record the *expected* installment schedule (dates/amounts) ahead of time for a Commitments/Planning calendar view. → **Sub-project 4** (Commitments & Planning).
- **Merchant/provider normalization** — `provider` is free text and not normalized across statements (e.g. the same payee can appear under slightly different strings month to month), which will make auto-linking transactions to the right Commitment or Debt unreliable without a normalization pass first. → **Sub-project 3** (classification engine), flagged in the v2 spec's own product-reference notes (Copilot).
- **Backfill of the 3,726 existing transactions** into Account/Commitment/Debt/Nature — explicitly deferred (see Out of Scope). → **Sub-project 3** or a dedicated later manual/scripted pass, same pattern as the bank-statement and electricity-history backfill scripts.
- **Revolut account** — a second real account exists (hit an API credit limit during the earlier bank-statement backfill and was never fully ingested) — relevant once `Account` rows actually get populated. → Data-entry/ingestion follow-up, not schema; tracked here so it isn't lost. (Same backfill project also still has 2 unrelated LLM type/category mix-ups sitting in `needs_attention`.)
- **Reclassifying known `OTHER_EXPENSE` outliers** (the €120,000 "CHEQUE COMPENSAÇÃO," €11,450 "MAFRA AUTO 3," €5,000 "ADIANTAMENTO"/"RECARGA" entries) once Debt/Person exist — several of these look like informal-debt candidates (the €5,000 SEPA transfer to a named individual, in particular) but classifying them is backfill work, out of scope here. → **Sub-project 3** or the same later manual pass as above.
