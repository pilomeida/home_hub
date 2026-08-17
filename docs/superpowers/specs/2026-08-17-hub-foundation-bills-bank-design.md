# Home & Family Hub — Foundation + Bills/Bank Statements — Design Spec

**Date:** 2026-08-17
**Status:** Approved for planning
**Scope:** Sub-project 1 of the Home & Family hub. Builds the shared foundation (auth, navigation, dashboard, wiki engine) together with the first real domain module (bills & bank statements), since the foundation shouldn't be designed in a vacuum. Health records and Home stuff are future sub-projects that plug into this same foundation — out of scope here.

## Context & Goals

Pedro wants a single web app, accessible to the whole family, that keeps a record and analysis of home/family affairs: bills, bank statements, health records, home maintenance/inventory, etc. It should:
- Be gated behind Cloudflare Access (no in-app login system)
- Organize content by domain (bills & bank, health, home, ...)
- Maintain an auto-extracted knowledge base ("wiki") of standing facts (e.g. current electricity provider & contract, insurance policy numbers) that stays current as new documents are ingested
- Surface dashboards/analysis and a to-do list

This spec covers the hub foundation and the first domain module: **bills & bank statements**.

## Access & Permissions

- Whole family, full visibility — no per-person privacy scoping. Anyone who passes Cloudflare Access sees all domains and all records.
- No in-app roles/permissions system for v1.

## Architecture

- Single FastAPI service, single SQLite database (one DB for the whole hub — not per domain — so cross-domain features like the wiki, dashboard, and to-dos can reference records across domains without cross-DB joins). Domains are separated by table/module within the one DB, not by database.
- Schema managed via **Alembic** migrations (not `create_all` as in Recipes) — this app accumulates real financial/health data over years across multiple evolving domains, so destructive schema changes carry more risk than in a single-domain app.
- Server-rendered with Jinja2, using **htmx** for dashboard interactivity (filtering spend by category/date, expanding to-dos) — avoids a JS build step while keeping the UI dynamic.
- Deployment: systemd + nginx on the same VPS as other family apps (Recipes, etc.), but as its own independent service and independent SQLite DB — no shared process or database with other apps.
- Sits behind a **Cloudflare Tunnel** (no public port exposed) with **Cloudflare Access** in front. New infra for this project — no existing domain/Cloudflare setup to reuse from Recipes.

### Auth

- An auth middleware reads the `Cf-Access-Jwt-Assertion` request header, verifies it against Cloudflare's JWKS endpoint for the Access application, and extracts the verified email.
- The verified email is mapped to a family member identity (simple static mapping — email → name) for attribution (e.g. "who uploaded this document"), not for access control (everyone sees everything).
- No password/login screen in the app itself.

### Navigation

Top-level sections: **Dashboard** (home) → **Bills & Bank** → **Wiki** → **To-Dos**. Health and Home sections are added as sibling top-level sections in later sub-projects, without restructuring this foundation.

## Data Model (Bills & Bank Statements domain)

- **Document** — a source file (uploaded, forwarded, or synced). Fields: source (`manual` / `email` / `api`), file reference, status (`pending` / `processed` / `needs_attention`), password-protected flag, uploaded/received timestamp, uploader identity.
- **Transaction** — an extracted line item linked back to its source `Document`: provider, category, amount, currency, due_date, paid_date, statement period.
- **Category** — controlled vocabulary (electricity, water, telecom, insurance, subscriptions, groceries, ...), LLM-assigned at extraction time. Same normalization pattern as the ingredient-normalization work already done in the Recipes app (multiple passes of cleanup/canonicalization expected over time).
- **BankAccount** — a linked account (Santander Portugal, Revolut), synced via Enable Banking.
- **BankTransaction** — transactions pulled from the linked bank accounts via API sync, structurally similar to `Transaction` but sourced from the API rather than document extraction.

## Wiki Engine

- **WikiPage** per topic (e.g. "Electricity — provider & contract", "Car Insurance"), holding the current set of facts plus a change history of superseded values.
- After a `Document` is extracted, a second pass ("is this wiki-worthy?") decides whether any extracted fact should update a wiki page — mirrors the harmonize-step pattern already used in the Recipes app for ingredient cleanup.
- Facts **auto-apply** immediately (no approval queue) and are marked with a "recently changed" indicator for a review window (e.g. 7 days) so a family member can glance and correct a bad extraction without it blocking the pipeline.

## Dashboard & To-Dos

- **Dashboard** shows: spend by category (this month vs. last), upcoming/overdue to-dos, recently changed wiki facts, and any documents stuck in `needs_attention`.
- **To-dos** auto-generate from `due_date` fields on transactions (e.g. "pay electricity bill by X"), plus manual add. Simple open/done status — no priorities or scheduling beyond due date in v1.

## Ingestion Pipeline

Three input channels, one shared pipeline:

1. **Manual upload** — drag/drop PDF or photo through the web app.
2. **Email forwarding** — a dedicated inbox (Cloudflare Email Routing → a processing endpoint) that family members can forward bills/statements to.
3. **Bank API sync** — scheduled sync via **Enable Banking** (Restricted Production tier — free, self-serve, scoped to accounts you personally link), pulling transactions for the linked **Santander Portugal** and **Revolut** accounts.

Common pipeline steps, regardless of source:
1. A `Document` (or `BankTransaction`, for API sync) row is created.
2. If the file is password-protected, attempt decryption trying each family member's NIF as the password before giving up.
3. Claude API extraction of structured data (provider, amounts, dates, line items) from the document.
4. Categorization against the controlled category vocabulary.
5. Wiki-fact pass (see above).
6. To-do generation from any due dates found.
7. Status updated to `processed`, or `needs_attention` if any step failed (bad decryption, low-confidence extraction) — failures are surfaced on the dashboard, never silently dropped.

### Deduplication

Duplicate document detection via content hash plus provider/period matching (e.g. re-uploading the same electricity bill twice should not create duplicate transactions).

## Bank API Integration Details

- Provider: **Enable Banking**, Restricted Production tier (free; scoped to accounts linked by the account holder themselves — matches this app's single-household use case exactly; no PSD2/TPP license needed).
- Confirmed supported ASPSPs: **Banco Santander Totta** (Portugal market, redirect-based auth via the Santander Portugal mobile app for SCA) and **Revolut** (registered under Enable Banking's Lithuania market, as Revolut is an EU-passported e-money institution — same account, same API).
- GoCardless Bank Account Data was considered but is closed to new signups as of mid-2025 and is being wound down — not viable for a new integration.

## Error Handling

- Any pipeline failure (decryption failure after trying all known NIFs, low-confidence or failed extraction, API sync error) sets the record's status to `needs_attention` and surfaces it on the dashboard. Nothing fails silently.
- Duplicate detection prevents double-counting re-ingested documents.

## Testing

- pytest unit tests for extraction, categorization, and wiki-fact-merge logic, following the existing test structure in the Recipes app.

## Open Questions / Follow-ups (not blocking)

- Exact review-window length for "recently changed" wiki facts (proposed: 7 days) — can be tuned after real usage.
- Cloudflare Tunnel/Access/Email Routing setup is new infra for this project and will need a domain decision as part of implementation planning.
