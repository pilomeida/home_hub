# Bank Statement & Multi-Transaction Ingestion — Design Spec

**Date:** 2026-08-18
**Status:** Approved for planning
**Scope:** Sub-project 2 of the Home & Family hub, extending sub-project 1 (Hub Foundation + Bills/Bank Statements manual channel). Adds support for multi-transaction bank statement documents (previously only single-transaction bills were supported), and replaces the PDF-to-image extraction mechanism with Claude's native PDF document support.

## Context & Goals

The manual-upload pipeline built in sub-project 1 assumes one PDF → one provider → one amount (a bill). Real bank statements — Santander Portugal "Extrato Consolidado" monthly statements, in this case — are multi-page documents containing dozens of individual transactions across multiple categories (debits, credits/income, and internal transfers between the user's own accounts, e.g. Santander ↔ Revolut). Feeding a statement through the existing single-transaction extraction produces meaningless output.

While investigating this, we discovered:
1. Real Claude Sonnet 5 responses can wrap JSON in a markdown code fence — already fixed in production (`app/services/json_utils.py`, applied to both `extraction.py` and `wiki_engine.py`).
2. Bills are not always single-page (issuer-dependent), and the existing `ensure_image` helper only converts page 1 of a PDF to an image — a real, if latent, bug for multi-page bills.
3. Claude's Messages API accepts a PDF directly as a `document` content block (no beta header, up to 600 pages on a 1M-context model like `claude-sonnet-5`), combining the PDF's visual layout **and** embedded text — strictly better than rasterizing to a single-page image, and the natural foundation for multi-page statement extraction too.

This spec covers: replacing the image-conversion extraction mechanism with native PDF document blocks, classifying an upload as a bill or a statement, and extracting multiple transactions from a statement.

## Document Input (shared foundation)

A new shared helper, `app/services/document_input.py`, builds the Claude content block for any uploaded file:
- `.pdf` files → `{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": ...}}` — the whole file, not a single page.
- Image files (`.png`, `.jpg`, `.jpeg`) → `{"type": "image", "source": {"type": "base64", "media_type": "image/...", "data": ...}}`, unchanged from today.

This replaces `ensure_image` (page-1-only PDF→PNG conversion via `pdf2image`/`poppler`) entirely. `extract_bill` (existing) is updated to use this helper — no behavior change for single-page bills, and multi-page bills are now read in full for the first time.

## Document Classification

Before extraction, `classify_document` (new, in `app/services/extraction.py`) sends the document content block to Claude Haiku with a short prompt asking it to classify the upload as `"bill"` (single provider, single amount) or `"statement"` (a bank account statement listing multiple transactions). Classification failure (malformed response, any exception) is treated the same as any other pipeline failure — the Document is marked `needs_attention`, never silently dropped or guessed.

## Statement Extraction

`extract_statement_transactions` (new, in `app/services/extraction.py`) sends the whole statement PDF as one document block and asks Claude Sonnet 5 for a JSON object: the statement's overall period (`"YYYY-MM"`) plus an array of every transaction line item, each with: transaction date, description (the counterparty/merchant), amount, currency, a `transaction_type` (`debit` | `credit` | `transfer` — `transfer` covers internal moves between the user's own accounts, e.g. Santander ↔ Revolut, so they're tracked but not counted as spend or income), and a category hint drawn from an expanded vocabulary (see below). No page splitting or looping is needed — the whole statement goes into one call.

If extraction fails (malformed JSON, wrong shape) the whole Document is marked `needs_attention` — there is no partial-recovery of individual line items; this mirrors the existing all-or-nothing philosophy for bill extraction and avoids the complexity of partially-ingested statements for v1.

## Data Model Changes

- **`Transaction.transaction_type`** (new column): `TransactionType` enum (`DEBIT` / `CREDIT` / `TRANSFER`), default `DEBIT`. Bill-derived transactions always hardcode `DEBIT` — no behavior change to the existing bill path.
- **`Category`** (existing enum, expanded): add `INCOME`, `TRANSFER`, `ATM_WITHDRAWAL`, `RESTAURANTS`, `SHOPPING`, `OTHER_EXPENSE` alongside the existing bill-oriented categories (electricity, water, gas, telecom, insurance, subscriptions, groceries, health, home, other).

**Migration note:** `Category` is stored via SQLAlchemy's `Enum` type, which on SQLite is backed by a `CHECK` constraint listing the allowed values at table-creation time. Adding new Python enum members does not by itself relax that constraint — the migration must rebuild the `transactions` table via Alembic's **batch mode** (`op.batch_alter_table(...)`), which requires adding `render_as_batch=True` to `alembic/env.py`'s `context.configure(...)` calls (both the online and offline branches, for consistency, though only the online path is exercised by this migration). The new `transaction_type` column itself is a plain `ADD COLUMN` and needs no batch mode.

## Pipeline Changes

`ingest_document` now classifies before extracting:

1. **Classification.** On failure, `needs_attention`, return.
2. **Bill path** (`doc_type == "bill"`) — unchanged from sub-project 1, except the created `Transaction` now explicitly sets `transaction_type=TransactionType.DEBIT`.
3. **Statement path** (`doc_type == "statement"`) — extract all line items; create one `Transaction` row per item (`provider` = the line's description, `category` via the existing `normalize_category`, `transaction_type` from the extracted type, `paid_date` = the line's transaction date, `statement_period` = the statement's overall period). Explicitly **skip** `find_duplicate_transaction` (many line items legitimately share provider/period within one statement — that check only makes sense for single-bill dedup), `generate_todo_for_transaction`, and `assess_and_update_wiki` (historical, already-settled data — no upcoming due date, no standing fact to record). Mark the Document `PROCESSED`.

Document-level content-hash dedup (already built in sub-project 1, runs before `ingest_document` is even called) continues to prevent re-processing an identical statement file — no new per-line-item dedup logic is needed.

## Dashboard Changes

`_spend_by_category` filters to `Transaction.transaction_type == TransactionType.DEBIT` only, so income and internal transfers don't inflate the "spend by category" view.

## Bills UI Changes

The bill detail page changes from "one optional Transaction" to "a list of Transactions" — this renders identically for an ordinary bill (a one-row list) and naturally extends to a statement (many rows), with no special-casing needed in the template.

## Out of Scope

- Partial-item recovery when a statement's transaction array is malformed (all-or-nothing, per above).
- A bulk-upload UI for the 49-statement historical backfill — handled by a one-off script that calls `ingest_document` directly (the same code path the web upload uses), not part of the reviewed application code. Naturally idempotent via the existing content-hash dedup, so a re-run after a partial failure just skips what's already ingested.
- Any change to the wiki engine, to-do generation, or the Enable Banking / email ingestion channels (still deferred from sub-project 1).
