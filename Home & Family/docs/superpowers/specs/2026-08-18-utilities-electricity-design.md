# Utilities Domain — Electricity Design Spec

**Date:** 2026-08-18
**Status:** Approved for planning
**Scope:** Sub-project 3 of the Home & Family hub. Adds a new "Utilities" domain — a nav tab with Electricity / Water / Telecom sub-tabs — starting with Electricity: a historical import of the user's existing consumption analysis, a data model that captures utility-specific detail (consumption + cost breakdown) beyond what the generic bill/Transaction model tracks, and a pipeline extension so future electricity bills keep that detail flowing in automatically.

## Context & Goals

The user has already built and maintained their own electricity consumption analysis outside the app: `Utilities/Electricity/Electricity consumption.xlsx`, covering 17 billed months (Nov 2024 – Jul 2026) for a Coopérnico BTN Simples 6,9 kVA contract, alongside the 18 source PDF invoices it was built from (one is a duplicate re-download). The workbook has a `Data` sheet (one row per billed month: consumption in kWh, total cost, cost/kWh, and a four-way cost breakdown — energy, power, fees & taxes, VAT) and a `Charts` sheet (7 bar charts: consumption, total cost, €/kWh, and each of the four cost components, all per month). The workbook's Notes document real editorial judgment calls: four months have no invoice on file and are deliberately left blank (Jan/May/Nov/Dec 2025); one invoice's printed fees box was corrected against the invoice detail; a credit note (-8.60€) was deliberately excluded from the monthly totals.

Goal: surface this analysis inside the hub as a first-class "Utilities → Electricity" view (table + charts), sourced from the workbook as ground truth for history — not re-derived from the PDFs, which would silently discard the judgment calls above — while making the pipeline ready to keep extracting the same level of detail (consumption + cost breakdown, not just amount/provider) from new electricity bills as they arrive from here on, most likely via the email-forwarding channel already planned as future work for this hub. Water and Telecom get the same nav structure now, as empty states, with no data source yet.

## Data Model

A new `UtilityReading` table, deliberately separate from `Transaction` — utility-specific fields (consumption, cost breakdown) don't belong on the generic bill/statement line-item model, and keeping them separate means Water/Telecom can reuse the same table later without bloating `Transaction` for every other bill category.

- `id`, `document_id` (FK to `documents.id`, linking each reading back to its source PDF)
- `utility_type`: enum `ELECTRICITY` / `WATER` / `TELECOM`
- `period_label`: `str`, `"YYYY-MM"` — the billing period's primary month (majority of days), the field every view groups/sorts/charts by. For historical import this comes directly from the workbook's `Month` column (parsed to `YYYY-MM`); for future extraction, the model is asked for this directly rather than re-derived via date math each time.
- `billing_period_start`, `billing_period_end`: `date`
- `invoice_number`: `Optional[str]`
- `consumption_value`: `Optional[float]`, `consumption_unit`: `Optional[str]` (e.g. `"kWh"` — generic on purpose, so Water's `m³` or Telecom's `GB` fit the same column later)
- `cost_total`: `float`
- `cost_per_unit`: `Optional[float]`
- `energy_cost`, `power_cost`, `fees_taxes_cost`, `vat_cost`: `Optional[float]` — nullable because not every utility bill has this exact four-way breakdown
- `created_at`: `datetime`

Every historical `UtilityReading` also gets a matching `Transaction` (`category=ELECTRICITY`, `transaction_type=DEBIT`, `amount=cost_total`, same `document_id`), so the existing Bills dashboard's spend-by-category view stays accurate for these months without a separate reconciliation step. Going forward, the pipeline creates both from the same bill upload (see below) — they are two views of the same event, not two competing sources.

## Pipeline Extension (future bills)

`_ingest_bill` (existing) is unchanged for its own responsibilities. After it successfully creates a `Transaction`, if that transaction's `category` is a utility category (`ELECTRICITY` today; `WATER`/`TELECOM` when those get wired up), the pipeline runs one additional extraction call — `extract_utility_detail(file_path, utility_type)` — against the same PDF, asking specifically for `period_label`, `billing_period_start/end`, `invoice_number`, `consumption_value`/`consumption_unit`, and the cost breakdown. On success, a `UtilityReading` is created linking to the same `document_id` and `Transaction`. On failure, this is enrichment, not a requirement: the bill's `Transaction` and `PROCESSED` status are unaffected, matching how todo/wiki enrichment failures already degrade gracefully elsewhere in this pipeline — the document does not go to `needs_attention` just because the richer utility detail couldn't be extracted.

## Historical Import (out of scope for the reviewed plan)

A one-off script — same category as the bank-statement backfill script, not part of the reviewed application code — imports the 17 billed rows from `Electricity consumption.xlsx` directly: skips the 4 blank months and the excluded credit note (respecting the workbook's own editorial calls rather than re-deriving them), matches each row to its source PDF (via the invoice number embedded in the PDF text, since filenames don't encode it — the one duplicate PDF resolves to whichever copy is content-identical), and creates a `Document` + `Transaction` + `UtilityReading` triple per row directly (no LLM extraction calls — the workbook is already-verified ground truth, and re-extracting would risk contradicting it). Content-hash dedup applies the same way it does everywhere else in this app, so the script is safe to re-run.

## UI

A new "Utilities" entry in the sidebar nav (`app/templates/base.html`), routing to `/utilities` → redirects to `/utilities/electricity`. Three sub-tabs (Electricity / Water / Telecom) rendered as an in-page tab strip, following the same nav pattern already established for Bills/Wiki/To-Dos. `/utilities/electricity` renders the monthly table (mirroring the workbook's `Data` sheet columns) and the 7 bar charts as plain server-rendered HTML/CSS bars — no new JS charting dependency; the app's only vendored JS today is `htmx.min.js`, and CSS bars are sufficient for single-series monthly bar charts. `/utilities/water` and `/utilities/telecom` render an empty state ("No data yet") — same pattern as the dashboard's existing empty-state handling — with no backing data source until the user adds bills for those utilities.

## Out of Scope

- Actual Water/Telecom data ingestion or analysis — nav structure and empty state only.
- The historical import script itself (personal one-off, as above).
- Any email-ingestion channel — the pipeline extension above only changes what happens to a bill once it's already been uploaded (manually, today), not how it arrives. Building the email-forwarding channel remains separately deferred work for this hub, as it was before this spec.
- Changes to `_ingest_bill`'s own logic, dedup, todo generation, or wiki assessment — the utility-detail extraction is strictly additive, after the existing bill path already succeeds.
