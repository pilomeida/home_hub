# Financial OS — Transactions UX + Classification Engine Design Spec

**Date:** 2026-08-21
**Status:** Approved for planning
**Scope:** Sub-project 3 of the Financial OS decomposition (`2026-08-21-financial-os-requirements-v2.md`). Builds the engine that populates the nullable `account_id`/`commitment_id`/`debt_id`/`nature` columns added by sub-project 1 (`2026-08-21-data-model-foundation-design.md`), adds merchant normalization, a standalone Transactions page with filters/bulk-edit/needs-review, and runs a full historical backfill over the existing 3,726 transactions.

## Context & Goals

Sub-project 1 shipped `Account`, `Person`, `Commitment`, `Debt`, and four new nullable `Transaction` columns — schema only, deliberately left unpopulated. Today, every one of the 3,726 existing transactions has `account_id`/`commitment_id`/`debt_id`/`nature` unset, `provider` is unnormalized free text (the same real merchant appears as `"MODELO HIPER 2640-MAFR"`, `"MODELO HIPER MAFRA"`, and `"MODELO HIPER"` — three different strings for one supermarket), and there is no UI anywhere to browse, filter, or edit transactions outside a single statement's detail page (`app/routers/bills.py`'s `bill_detail`).

Goal: build a merchant-resolution + classification engine that assigns these fields (with a human-reviewable fallback for anything uncertain), a standalone Transactions page to browse/filter/bulk-edit/review, and run the engine once over all existing history so the Overview and other future screens have real data to work with — not just an empty pipe.

## Data Model Additions

### `Merchant` (new table)

The canonical identity a raw `provider` string resolves to.

- `id`
- `canonical_name`: `str` (e.g. `"Modelo Hiper"`)
- `default_category`: `Category` (reuses the existing enum)
- `default_nature`: `Optional[Nature]`
- `normalized_key`: `str`, unique — the exact-match lookup key produced by `normalize_provider()`
- `created_at`

Once a `Merchant` exists, its `default_category`/`default_nature` apply to every future transaction resolving to it — most transactions get classified without an LLM call after the first time a merchant is seen.

### `Transaction.merchant_id` (new nullable FK)

Alongside the existing `account_id`/`commitment_id`/`debt_id`/`nature` from sub-project 1.

### `Document.account_id` (new nullable FK) — a gap found while designing this sub-project

Nothing today links a `Document` (statement/bill) to an `Account`. This matters because `account_id` answers "which of your accounts did this move through," a property of the *statement*, not of the individual transaction's payee — merchant/provider text can never answer it. `Document.account_id` is set via a dropdown at upload time (`bills/upload.html`); `Transaction.account_id` simply inherits its parent `Document.account_id` at ingestion/backfill time. All 45 existing documents are, in reality, from the single fully-ingested account, so the backfill assigns that account to all of them as a one-time manual/scripted step before transaction-level backfill runs.

## Provider → Merchant Resolution (the "rules" tier)

A pure function, `normalize_provider(raw: str) -> str`, strips statement-specific noise — trailing store/location codes (`"2640-MAFR"`, `"MAFRA"`), reference numbers, common suffixes — via a small, testable set of regex/keyword rules, producing a normalized key.

- **Lookup hits** `Merchant.normalized_key` (unique, exact match): reuse that `Merchant`'s `id`, `default_category`, `default_nature`. No LLM call.
- **Lookup misses**: fall through to the LLM tier — ask Claude for a canonical name, category, and nature given the raw provider string (and, where useful, sibling transactions' amounts for context), create a new `Merchant` row from the response, then classify this transaction against it. Every future occurrence of that merchant resolves via the rules tier from then on (the normalized key now exists in `Merchant`), so an LLM call happens at most once per distinct merchant, not once per transaction — this is what keeps the full 3,726-row backfill's API cost bounded to roughly the number of *distinct* merchants, not the number of transactions.

`classify_transaction(session, transaction) -> ClassificationResult` is the single entry point implementing both tiers, used identically by the live ingestion pipeline and the backfill script (Approach B from the design discussion — one function, two callers, so the two never drift apart the way a duplicated implementation would).

This sub-project does not touch `Category` assignment (`app/services/categorization.py`'s `normalize_category`, already run at ingestion) — merchant resolution and `Category` are orthogonal; a `Merchant`'s `default_category` is a *new, independent* suggestion surfaced for review, not a silent override of the LLM-extracted category already stored on existing transactions.

## Recurring-Commitment & Debt-Candidate Detection

Both heuristics run after merchant resolution, as a second pass over already-`merchant_id`-resolved transactions (live: after each ingestion; backfill: after the full merchant-resolution pass completes).

- **Recurring-commitment candidate**: group transactions by `merchant_id`; flag when the same merchant appears in 3 or more consecutive `statement_period`s with every amount in that run within ±10% of the run's average. Surfaced in Needs Review as "looks recurring — create a Commitment?" — never auto-created; the yearly-vs-recurring/scale judgment (v1 spec's IMI-vs-weekend-break distinction) stays a human call.
- **Debt candidate**: pattern-match raw `provider` text for personal-transfer markers common in Portuguese bank statement phrasing (`"P/ <Name>"`, `"TRF ... P/"`), combined with `category` in `{TRANSFER, OTHER_EXPENSE}` and `amount > €500` (filters out small MB WAY payments to friends that aren't debt). Surfaced in Needs Review as "looks like a person-to-person transfer — link to a Debt?" — never auto-linked; matches the real `"TRF CRED SEPA+ P/ EDUARDO MANUEL DA SILVA €5,000"` example found in the data during sub-project 1's brainstorm.

## Needs Review Queue

A transaction lands in Needs Review when any of:
1. Neither the rules nor the LLM tier could confidently resolve a `Merchant` (classification genuinely uncertain).
2. The merchant is brand new (first time this `Merchant` row was created) — a human nod on the LLM's proposed category/nature before it becomes every future transaction's silent default.
3. Recurring-commitment candidate detected (above).
4. Debt candidate detected (above).

Each row is actionable inline: confirm the proposed classification, edit it, create a `Commitment` (case 3) or link/create a `Debt` (case 4), or dismiss.

## Transactions UI

New standalone `/transactions` nav entry (sibling to Bills/Utilities/Wiki/To-Dos, not nested under Bills) — a new page is warranted since Overview (sub-project 2) doesn't exist as real code yet, and Overview will later *link into* filtered views of this page rather than duplicating the queue itself:

- **Table view**: filterable by date range, `Category`, `Nature`, `Account`, and needs-review status. Every row shows provider, resolved merchant (once resolved), category, nature, amount, account.
- **Bulk edit**: checkbox multi-select + an action bar to apply `Category`/`Account`/`Commitment`/`Nature` to every selected row in one action.
- **Needs Review tab**: the queue described above, one actionable row per flagged transaction.

## Backfill

A one-off script (`scripts/backfill_transaction_classification.py`, matching the `scripts/backfill_electricity_history.py` precedent — personal one-off, not part of the reviewed application's live code path):

1. Assigns the single real `Account` to all 45 existing `Document` rows (one-time, since all are from that account in reality).
2. Runs `classify_transaction()` across all 3,726 existing transactions, populating `merchant_id`, `account_id` (inherited from each transaction's document), `nature`, and queuing anything meeting a Needs Review trigger.
3. Idempotent via `merchant_id IS NULL` — safe to re-run if interrupted partway (mirrors the dedup-safety pattern already established for the bank-statement and electricity backfills).

`commitment_id`/`debt_id` are never set by the backfill directly — recurring-commitment and debt candidates surface in Needs Review for a human to confirm, consistent with "never auto-create/auto-link" above.

## Migrations

Additive only: 1 new table (`merchants`), 2 new nullable columns (`transactions.merchant_id`, `documents.account_id`). No column removals, no changes to existing enums, no changes to `Category` assignment logic.

## Out of Scope

- Changing `Category` assignment itself (`normalize_category` stays as-is; `Merchant.default_category` is a new, separate, reviewable suggestion, not a silent override of already-extracted categories).
- Building Overview (sub-project 2) — this page is designed to be link-into-able from Overview later, not to pre-build any part of Overview itself.
- Auto-creating `Commitment` or `Debt` rows, or auto-assigning `debt_id` — every candidate the engine finds is surfaced for a human decision, never applied automatically.
- Water/Telecom utility-specific merchant handling beyond what the generic `Merchant`/`Category` mechanism already covers (Utilities domain enrichment is sub-project-2-adjacent, already handled separately per `2026-08-18-utilities-electricity-design.md`).
- A real-time/streaming classification mode — the engine runs synchronously at ingestion time (mirroring how `normalize_category` already runs) and as a one-off backfill script; no background job queue.

## Not Yet Decided / Explicitly Deferred

- The exact LLM prompt/response schema for the merchant-resolution fallback tier (canonical name + category + nature) — an implementation-plan-level detail, not a design-level one.
- Whether `normalize_provider()`'s regex/keyword rule set needs per-bank-format variants once a second real account (Revolut) is fully ingested — today's rules are grounded only in the single account's `"EXTCON..."`-format statements.
- Any UI treatment for `Merchant` itself (a browse/edit view for the `merchants` table) — not designed here; the Transactions page surfaces resolved merchant names inline, but managing `Merchant` rows directly is not in this sub-project's UI scope.
