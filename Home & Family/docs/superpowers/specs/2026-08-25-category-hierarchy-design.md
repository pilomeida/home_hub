# Category / Sub-category Hierarchy — Design

**Date:** 2026-08-25
**Status:** Approved, ready for planning
**Scope:** Replaces the flat 16-value `Category` enum (`app/models/transaction.py`) with a
DB-backed two-level hierarchy (Category > Sub-category), plus a cross-cutting Tag mechanism,
touching `Transaction`, `Merchant`, `Commitment`, the classification engine, the Overview
category-comparison logic, and every category-selecting UI surface.

## Background

The hierarchy's actual content — which top-level categories exist, which sub-categories belong to
each, and which old flat category each maps from — was iterated to agreement with Pedro via a
shared spreadsheet before this spec was written. That content is reproduced verbatim in "Seed
Data" below; it is not open for renegotiation here. What this spec defines is purely the
engineering approach: schema, migration, and every code path that currently assumes a flat
`Category` enum.

## Global Constraints

- Every new column is nullable and every migration is additive (no drops, no renames of existing
  columns) — matches this project's established migration discipline (see
  `docs/ARCHITECTURE.md`'s "Design Decisions").
- No SQLModel `Relationship` anywhere — cross-table references are plain FK columns, resolved via
  `{id: value}` lookup dicts built in routers, exactly like every other model in this codebase.
- No new frontend dependency. Cascading dropdowns are implemented with htmx, the only pattern this
  app already uses for dynamic, server-rendered UI updates (see the Overview's range pills, the
  Transactions bulk-edit re-render).
- `alembic/versions/` stays a single linear chain; `render_as_batch=True` (SQLite).
- Real production data: ~4,850 transactions, ~1,160 merchants, 0 Commitments as of this writing.
  Any migration script must be idempotent and tested against a scratch copy of the real database
  before running against production, per this project's established discipline.

## Schema

### `Category` (new table — the top level, e.g. "Housing")

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `name` | `str`, unique | e.g. "Housing", "Income", "Debt & Transfers" |
| `counts_as_spend` | `bool`, default `True` | `False` for **Income** and **Debt & Transfers** — replaces the current `_COMPARISON_EXCLUDED_CATEGORIES` hardcoded-enum-member approach in `overview_service.py` with a real flag that survives the hierarchy no longer being a fixed enum |

### `SubCategory` (new table — the child, e.g. "Mortgage")

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `category_id` | FK -> `categories.id`, indexed | |
| `name` | `str` | Unique **within its parent category**, not globally (e.g. "Restaurants" the sub-category under "Restaurants" the top-level is fine) |
| `direction` | enum: `INCOME` / `EXPENSE` / `INFLOW` / `OUTFLOW` / `INTERNAL` | Matches the spreadsheet's per-row Type column. Every sub-category outside "Debt & Transfers" is uniformly `INCOME` (for the Income group) or `EXPENSE`; "Debt & Transfers" mixes `INFLOW` (Loan Received, Loan Repayment Received), `OUTFLOW` (Loan Repayment Made, ATM Withdrawal), and `INTERNAL` (Internal Transfer) |

### `Tag` + `SubCategoryTag` (new tables — generic cross-cutting labels)

| Table | Columns | Notes |
|---|---|---|
| `Tag` | `id`, `name` (unique) | Seeded with exactly one row for this spec: `"Insurance"`. Not Insurance-specific as a mechanism — any future cross-cutting grouping reuses these same two tables. |
| `SubCategoryTag` | `sub_category_id` FK, `tag_id` FK, composite PK | Join table. Seeded rows: (Property Insurance, Insurance), (Health Insurance, Insurance), (Vehicle Insurance, Insurance) |

### Changes to existing tables

- **`Transaction`**: drop reliance on the `category: Category` enum column's *type* (the column
  stays, see Migration below) in favor of two new nullable FKs: `category_id -> categories.id`,
  `sub_category_id -> sub_categories.id`.
- **`Merchant`**: `default_category: Category` becomes `default_category_id` + `default_sub_category_id`
  (both nullable FKs), same shape as Transaction.
- **`Commitment`**: `category: Category` becomes `category_id` + `sub_category_id` (both nullable
  FKs), same shape.

The old `category`/`default_category` enum *columns* on `Transaction`, `Merchant`, and `Commitment`
are kept, unused by the app after migration, as a historical/audit trail — dropping them is
explicitly **out of scope** for this spec, a future cleanup once the new columns have been live
long enough to trust fully. The `Category` Python enum *class* itself is retired from
`app/models/transaction.py` once nothing in the app reads it (the column's stored values remain
plain strings at the DB level regardless — SQLModel enum columns are stored as their string values,
so removing the Python enum class doesn't affect the already-written column data).
`TransactionType` (DEBIT/CREDIT/TRANSFER) and `Nature` (ESSENTIAL/DISCRETIONARY) are unrelated to
this change and stay exactly as they are.

## Seed Data

Inserted by a data-only Alembic migration (schema migration + data migration can be two separate
revisions, or one — implementer's call, either is additive and reversible). Source of truth for
this content: `build_category_hierarchy.py`'s `HIERARCHY` list (the script that generated the
spreadsheet delivered to and approved by Pedro this session) — implementers should port that exact
Python literal rather than re-deriving it by hand. Full content, in the final agreed form (A-Z at
both levels), `(sub-category, direction, tag)`:

**Debt & Transfers** (`counts_as_spend=False`): ATM Withdrawal (Outflow), Internal Transfer
(Internal), Loan Received (Inflow), Loan Repayment Made (Outflow), Loan Repayment Received (Inflow)

**Education** (`counts_as_spend=True`): Books (Expense), Extracurricular Activities (Expense),
Stationery & Supplies (Expense), Tuition (Expense)

**Groceries** (`counts_as_spend=True`): Specialty & Local (Expense), Supermarket (Expense)

**Health** (`counts_as_spend=True`): Dental (Expense), Doctor Consultations (Expense), Health
Insurance (Expense, tag: Insurance), Medication & Pharmacy (Expense), Therapy & Psychology (Expense)

**Housing** (`counts_as_spend=True`): Condo Fees (Expense), Furniture & Appliances (Expense),
Maintenance & Repairs (Expense), Mortgage (Expense), Property Insurance (Expense, tag: Insurance),
Property Tax (Expense)

**Income** (`counts_as_spend=False`): Freelance & Practice Income (Income), Government & Social
Security (Income), Investment Income (Income), Other Income (Income), Refunds & Reimbursements
(Income), Rental Income (Income), Salary & Employment (Income)

**Other** (`counts_as_spend=True`): Other Expense (Expense), Uncategorized (Expense)

**Restaurants** (`counts_as_spend=True`): Cafes & Coffee (Expense), Restaurants (Expense), Takeout
& Delivery (Expense)

**Shopping** (`counts_as_spend=True`): Clothing (Expense), Electronics (Expense), Gifts (Expense),
Home Goods (Expense)

**Subscriptions** (`counts_as_spend=True`): Memberships (Expense), Software (Expense), Streaming
(Expense)

**Transport** (`counts_as_spend=True`): Fuel (Expense), Public Transport (Expense), Tolls & Parking
(Expense), Vehicle Insurance (Expense, tag: Insurance), Vehicle Maintenance (Expense)

**Utilities** (`counts_as_spend=True`): Electricity (Expense), Gas (Expense), Internet & Mobile
(Expense), Water (Expense)

## Migrating the ~4,850 Existing Transactions

A one-off script (following the established pattern of `scripts/backfill_transaction_classification.py`
and `scripts/merge_duplicate_merchants.py` — not reviewed application code, its own docstring,
idempotent, tested against a scratch copy first) sets `category_id` (and `sub_category_id` where
unambiguous) from each transaction's old flat `category` enum value:

| Old `Category` value | New `category_id` | New `sub_category_id` |
|---|---|---|
| `ELECTRICITY` | Utilities | Electricity |
| `WATER` | Utilities | Water |
| `GAS` | Utilities | Gas |
| `TELECOM` | Utilities | Internet & Mobile |
| `GROCERIES` | Groceries | *(left NULL — Supermarket vs. Specialty & Local isn't distinguishable from the old flat value alone)* |
| `RESTAURANTS` | Restaurants | *(left NULL — Restaurants vs. Cafes & Coffee vs. Takeout & Delivery isn't distinguishable from the old flat value alone)* |
| `SUBSCRIPTIONS` | Subscriptions | *(left NULL — Streaming/Software/Memberships aren't distinguishable from the old flat value alone)* |
| `TRANSFER` | Debt & Transfers | Internal Transfer |
| `ATM_WITHDRAWAL` | Debt & Transfers | ATM Withdrawal |
| `INSURANCE` | *(left NULL)* | *(left NULL — could be Property/Health/Vehicle, genuinely ambiguous)* |
| `HOME` | Housing | *(left NULL)* |
| `INCOME` | Income | *(left NULL)* |
| `HEALTH` | Health | *(left NULL)* |
| `SHOPPING` | Shopping | *(left NULL)* |
| `OTHER_EXPENSE` | *(left NULL)* | *(left NULL — this was the catch-all; its members could land in Education, Transport, Debt & Transfers' Loan Repayment Made, or genuinely Other)* |
| `OTHER` | Other | Uncategorized |

Every transaction left with `sub_category_id IS NULL` after this script surfaces in the Needs
Review queue's new "Needs sub-category" section (see below) for Pedro to assign by hand — no
guessed defaults, per his explicit choice.

## Classification Engine Changes (`app/services/classification_engine.py`)

`resolve_merchant_via_llm` currently asks Claude to pick one of 16 hardcoded flat category strings
from a string baked into `_MERCHANT_SYSTEM_PROMPT`. It now:

1. Queries the DB for the current `Category`/`SubCategory` tree at call time (or the caller passes
   it in — implementer's call on the exact function boundary) and renders it into the system
   prompt as a nested list, so the prompt can never drift out of sync with the DB.
2. Asks Claude to return `category` (top-level name) **and** `sub_category` (name, must be a valid
   child of the returned category) instead of one flat `category` string.
3. `Merchant.default_category_id`/`default_sub_category_id` get set from the resolved names via a
   DB lookup (case-sensitive exact match against the seeded names is fine — this is a controlled
   vocabulary, not free text).
4. If Claude returns a `(category, sub_category)` pair that doesn't validate against the real
   parent/child relationship (hallucination), fall back to `sub_category_id = NULL` with
   `category_id` set if that alone validates, or both `NULL` — surfacing in Needs Review rather
   than raising, matching how a`MerchantResolutionError` already surfaces failures today.

`app/services/categorization.py`'s `normalize_category` (used for the free-text `category_hint` a
bill/statement extraction call returns, independent of merchant-based resolution) gets an
equivalent rework: its synonym dict maps a free-text hint to a `(category_name, sub_category_name)`
pair where a confident mapping exists (e.g. `"electricity"` -> `(Utilities, Electricity)`), and to
`(category_name, None)` or `(None, None)` where it doesn't — never guessing a sub-category it isn't
confident about.

## Overview Service Changes (`app/services/overview_service.py`)

`_COMPARISON_EXCLUDED_CATEGORIES = (Category.TRANSFER, Category.ATM_WITHDRAWAL, Category.INCOME)`
(a tuple of enum members) is replaced by a query-time join filtering on `Category.counts_as_spend
== False`. Every other place `Transaction.category` is read for display (KPI drill-down URLs,
category-comparison rows) switches from `.category.value` (the enum's string) to the joined
`Category.name` (and, where relevant, `SubCategory.name`) via the existing no-Relationship lookup-dict
convention.

## UI: Cascading Category -> Sub-category Selection

Every place a flat category `<select>` exists today (Transactions bulk-edit, Transactions list
filter, Needs Review's per-merchant confirm dropdown) gains a second `<select>` for sub-category,
wired the same way this app already does dynamic dropdown-driven re-rendering: the Category
`<select>` gets `hx-get` (e.g. `GET /categories/{category_id}/sub-categories`) targeting the
Sub-category `<select>`'s options, `hx-trigger="change"`. No new JS pattern — this mirrors the
Overview's range-pill htmx swaps and the existing bulk-edit re-render exactly.

## Needs Review: New "Needs Sub-category" Section

A new section in `get_needs_review_queue()`'s `NeedsReviewQueue` dataclass and
`_needs_review_rows.html`, listing transactions where `category_id IS NOT NULL AND
sub_category_id IS NULL` (deliberately distinct from the existing "unclassified" list, which is
`merchant_id IS NULL` — a transaction can have a resolved merchant and category, and still be
missing only its sub-category). Each row offers a sub-category `<select>` scoped to that
transaction's already-known `category_id` (cascading, per above) plus a save action.

## Testing

- Schema/model tests follow the existing `tests/conftest.py` convention (fresh `SQLModel.metadata`
  per test, not via Alembic).
- The seed migration is verified against a scratch copy of the real database before running
  against production, per established discipline — never against the live file directly during
  development.
- Classification-engine tests mock the Anthropic client the same way existing tests already do
  (see `test_classification_engine.py`'s existing pattern), extended to assert the new
  `(category, sub_category)` pair resolution and the hallucination-fallback path.
- Overview tests updated wherever they currently construct a `Transaction(category=Category.X, ...)`
  literal — these become `Transaction(category_id=..., sub_category_id=..., ...)` fixture setup,
  a mechanical but wide-reaching test update given how many existing tests build transactions this
  way.
