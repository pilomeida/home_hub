# Home & Family Hub — Architecture

Technical reference for developers and AI assistants extending or debugging this system.

---

## System Overview

```
Browser
  │  HTTPS, behind Cloudflare Access (JWT in Cf-Access-Jwt-Assertion / CF_Authorization cookie)
  ▼
FastAPI  (app/main.py)
  ├── CloudflareAccessMiddleware  (app/auth.py) — every request except /health
  │
  ├── GET  /                       ── app/routers/dashboard.py    (spend summary, open todos, needs-attention)
  ├── GET/POST /bills/*            ── app/routers/bills.py        (upload, list, detail — the ingestion entry point)
  ├── GET/POST /transactions/*     ── app/routers/transactions.py (browse/filter/bulk-edit, Needs Review queue)
  ├── GET  /utilities/{tab}        ── app/routers/utilities.py    (Electricity/Water/Telecom consumption + charts)
  ├── GET  /wiki/*                 ── app/routers/wiki.py         (auto-maintained standing-facts pages)
  ├── GET/POST /todos/*            ── app/routers/todos.py        (due-date-driven task list)
  │
  └── Static: /static/*  (htmx.min.js; app/static/documents/ — uploaded source PDFs)

Ingestion pipeline (app/services/pipeline.py — the single entry point every
upload goes through, `ingest_document()`):

  classify_document (Claude Haiku: bill vs statement)
        │
        ├─ "bill"      → extract_bill → dedup check → Transaction (1) → classify_transaction
        │                                                    │              │
        │                                          [if utility category]   └─ merchant_id/nature
        │                                          extract_utility_detail (enrichment, non-fatal)
        │                                                    │
        │                                          generate_todo_for_transaction
        │                                          assess_and_update_wiki
        │
        └─ "statement" → extract_statement_transactions → Transaction (N, per-item commit)
                                                                │
                                                          classify_transaction (per item)

Classification engine (app/services/classification_engine.py — populates
merchant_id / account_id / nature on every Transaction; also the standalone
detection heuristics behind the Needs Review queue):

  classify_transaction(transaction)
        │
        ├─ normalize_provider(raw)  ──►  Merchant.normalized_key lookup (rules tier, no LLM)
        │                                        │ hit                    │ miss
        │                                        ▼                        ▼
        │                              reuse Merchant           resolve_merchant_via_llm (Claude Haiku)
        │                                                                 │
        │                                                        create new Merchant (confirmed=False)
        │
        ├─ inherit account_id from parent Document (only if unset)
        └─ set nature from Merchant.default_nature (only if unset)

  detect_recurring_candidates()  — 3+ consecutive months, same merchant, amounts within ±10% of the run's average
  detect_debt_candidates()       — category ∈ {TRANSFER, OTHER_EXPENSE}, amount > €500, "P/ <Name>"-style regex match
  get_needs_review_queue()       — aggregates: unconfirmed merchants + recurring candidates + debt candidates
                                    + transactions where merchant_id IS NULL (classification failed)

Data layer: SQLite (data/home_family.db) via SQLModel/SQLAlchemy, migrated with Alembic.
Config: app/config.py ← reads .env at startup (ANTHROPIC_API_KEY required; DATABASE_PATH,
DOCUMENTS_DIR, CF_ACCESS_* optional).
```

The system is **single-process, no queue/worker daemon**. All ingestion (including the multi-call
extraction + classification) runs synchronously inside the request handler for `POST /bills/upload`,
or inside a one-off script's own event loop for backfills.

---

## Repository Layout

```
Home & Family/
├── app/
│   ├── main.py                FastAPI app init; CORS-free (Cloudflare Access gates access);
│   │                           registers CloudflareAccessMiddleware + all routers; mounts /static
│   ├── config.py               Settings from .env (ANTHROPIC_API_KEY, DATABASE_PATH, DOCUMENTS_DIR,
│   │                           CF_ACCESS_TEAM_DOMAIN, CF_ACCESS_AUD)
│   ├── db.py                   SQLModel engine + get_session() FastAPI dependency; registers the
│   │                           PRAGMA foreign_keys=ON connect-event listener
│   ├── auth.py                 CloudflareAccessMiddleware — verifies the CF Access JWT via JWKS,
│   │                           attaches request.state.user_email; skips only /health
│   │
│   ├── models/                 One file per entity; app/models/__init__.py imports every module so
│   │   │                       SQLModel.metadata is fully populated (required for create_all/alembic)
│   │   ├── document.py         Document: a source file (manual/email/api), status lifecycle
│   │   │                       PENDING → PROCESSED | NEEDS_ATTENTION; account_id (which bank account
│   │   │                       this statement belongs to)
│   │   ├── transaction.py      Transaction: one line item. Category (16-value enum) + Nature
│   │   │                       (essential/discretionary, orthogonal to Category) + TransactionType
│   │   │                       (debit/credit/transfer) + account_id/commitment_id/debt_id/merchant_id
│   │   │                       (all nullable FKs) + debt_candidate_reviewed
│   │   ├── account.py          Account: a real bank account/wallet (name, institution, currency,
│   │   │                       account_type, identifier)
│   │   ├── merchant.py         Merchant: the canonical identity a raw provider string resolves to
│   │   │                       (canonical_name, default_category, default_nature, normalized_key
│   │   │                       unique, confirmed, recurring_reviewed)
│   │   ├── person.py           Person: lightweight counterparty for informal debt (name, notes)
│   │   ├── commitment.py       Commitment: planned recurring/yearly spend (name, category, cadence
│   │   │                       enum, planned_amount, year — set only for YEARLY cadence, one row
│   │   │                       per year, never an evergreen template)
│   │   ├── debt.py             Debt: formal (→ commitment_id, a scheduled payment) or informal
│   │   │                       (→ person_id + direction, no schedule); current_balance is
│   │   │                       sa.Numeric(12,2), not float (it's a running accumulator)
│   │   ├── utility_reading.py  UtilityReading: consumption + cost-breakdown detail beyond what
│   │   │                       Transaction tracks, one row per billed month per utility_type
│   │   ├── todo.py             Todo: title, due_date, done, optional transaction_id
│   │   └── wiki.py             WikiPage (current facts per topic) + WikiChange (history)
│   │
│   ├── routers/                One file per nav section; each owns its own Jinja2Templates instance
│   │   ├── dashboard.py        `/` and `/health`
│   │   ├── bills.py            `/bills*` — upload form + POST (dedup by content_hash, then
│   │   │                       ingest_document), list, per-document detail
│   │   ├── transactions.py     `/transactions*` — filtered/paginated list, bulk-edit, Needs Review
│   │   │                       tab (confirm/dismiss/create-commitment/link-debt actions)
│   │   ├── utilities.py        `/utilities/{tab}` — Electricity/Water/Telecom table + bar charts
│   │   ├── wiki.py             `/wiki*` — page list + detail with change history
│   │   └── todos.py            `/todos*` — open/done lists, mark-done (htmx partial swap)
│   │
│   ├── services/                Business logic, no HTTP/template concerns
│   │   ├── pipeline.py          ingest_document() — the ingestion orchestrator (see diagram above)
│   │   ├── extraction.py        Every Claude call for document understanding: classify_document,
│   │   │                       extract_bill, extract_statement_transactions, extract_utility_detail
│   │   ├── classification_engine.py  normalize_provider, resolve_merchant_via_llm,
│   │   │                       classify_transaction, detect_recurring_candidates,
│   │   │                       detect_debt_candidates, get_needs_review_queue
│   │   ├── categorization.py    normalize_category() — synonym-dict mapping of an LLM category_hint
│   │   │                       to the Category enum (separate from, and unrelated to, Merchant
│   │   │                       resolution — Category is set at ingestion, Merchant afterward)
│   │   ├── dedup.py             find_existing_document_by_hash (upload-time, by content_hash),
│   │   │                       find_duplicate_transaction (bill re-upload, by provider+period,
│   │   │                       excluding statement-derived line items)
│   │   ├── todo_engine.py       generate_todo_for_transaction — one Todo per due_date
│   │   ├── wiki_engine.py       assess_and_update_wiki — second-pass Claude call deciding if a
│   │   │                       document holds a standing fact (vs. purely transactional data)
│   │   ├── dashboard_service.py get_dashboard_data — spend-by-category (this/last month),
│   │   │                       open todos, recently-changed wiki pages, needs-attention documents
│   │   ├── document_input.py    build_content_block — base64 PDF/image → Claude content block
│   │   ├── json_utils.py        strip_json_fences — strips a ```json fence if Claude adds one
│   │   └── storage.py           save_upload — writes to DOCUMENTS_DIR, returns (path, sha256 hash)
│   │
│   ├── templates/               Jinja2, server-rendered, htmx for partial-swap interactivity
│   │   ├── base.html            Nav shell; loads htmx.min.js; shared CSS
│   │   ├── dashboard.html
│   │   ├── bills/{list,upload,detail}.html
│   │   ├── transactions/
│   │   │   ├── list.html                 Filters (GET form) + bulk-edit (htmx POST form, paginated)
│   │   │   ├── _rows.html                 Table body partial (swapped in after bulk-edit)
│   │   │   ├── needs_review.html
│   │   │   └── _needs_review_rows.html    4 sections: unconfirmed merchants, recurring candidates
│   │   │                                 (+ create-Commitment form), debt candidates (+ link-Debt
│   │   │                                 form), unclassified transactions (read-only)
│   │   ├── utilities/tab.html
│   │   ├── wiki/{list,page}.html
│   │   └── todos/{list,_lists}.html
│   │
│   └── static/
│       ├── htmx.min.js          The app's only vendored JS dependency
│       └── documents/           Uploaded/imported source files (gitignored; content-hash-named)
│
├── alembic/
│   ├── env.py                   render_as_batch=True (required for SQLite ALTER TABLE); reads
│   │                           settings.database_url; imports app.models so metadata is complete
│   └── versions/                16 linear migrations, no branches — see Database Schema below
│
├── scripts/                      One-off historical-import scripts. NOT part of the reviewed
│   │                           application code (no test coverage expected) — each has its own
│   │                           docstring explaining why it exists and whether it's re-runnable.
│   ├── backfill_electricity_history.py       Excel (ground truth) → Document+Transaction+
│   │                                         UtilityReading, no LLM calls
│   ├── backfill_transaction_classification.py Runs classify_transaction() over every
│   │                                         Transaction/Document missing it. Idempotent
│   │                                         (merchant_id IS NULL). The SAME function the live
│   │                                         pipeline uses — never a second implementation.
│   ├── merge_duplicate_merchants.py           Merges exact-case-insensitive-canonical_name
│   │                                         duplicate Merchant rows (normalize_provider's rules
│   │                                         tier doesn't catch every real format variant).
│   │                                         Idempotent by construction.
│   ├── backfill_documents.py                  (untracked — see docs/SYSADMIN.md)
│   └── backfill_revolut_pedro_account.py      (untracked — see docs/SYSADMIN.md)
│
├── tests/                        pytest; `session`/`client`/`engine` fixtures in conftest.py build
│   │                           a fresh SQLite DB per test from SQLModel.metadata (NOT via Alembic —
│   │                           migrations are verified separately, by hand, against scratch copies
│   │                           of the real database during development)
│   └── test_*.py                One file per model/service/router, matching the app/ layout
│
├── docs/
│   ├── ARCHITECTURE.md           This file
│   ├── SYSADMIN.md                Operational reference — deployment, backups, accounts, costs
│   └── superpowers/
│       ├── specs/                Design docs (one per sub-project), each superseding/refining the
│       │                       previous. Read these for the *why* behind a design.
│       └── plans/                Task-by-task implementation plans generated from each spec
│
├── .github/workflows/deploy.yml   CI/CD — see docs/SYSADMIN.md
├── alembic.ini
├── pyproject.toml                 pytest config only
├── requirements.txt
└── data/home_family.db            The real SQLite database (gitignored)
```

---

## Database Schema

Single file: `data/home_family.db` (SQLite). Additive-only migration history (16 revisions, one
linear chain, no branches) — every schema change to date has been a new table or a new nullable
column; nothing has ever been dropped or had an existing column's meaning changed.

### `documents`
A source file (uploaded, forwarded, or synced).

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `filename`, `file_path` | TEXT | `file_path` is under `app/static/documents/`, content-hash-named |
| `content_hash` | TEXT, indexed | SHA-256 of the file bytes — upload-time dedup key |
| `source` | enum | MANUAL / EMAIL / API |
| `status` | enum | PENDING → PROCESSED \| NEEDS_ATTENTION |
| `doc_type` | TEXT, nullable | "bill" or "statement", set by `classify_document`; nullable because it didn't exist before an early migration — pre-migration rows are treated as "bill" by dedup's NULL-safe filter |
| `password_protected` | bool | |
| `failure_reason` | TEXT, nullable | Populated whenever status is NEEDS_ATTENTION, or as a non-fatal note when PROCESSED with degraded enrichment |
| `uploaded_by` | TEXT, nullable | CF Access email, or a script name for backfills |
| `account_id` | INT, nullable FK → `accounts.id` | Which bank account this statement belongs to; `Transaction.account_id` inherits from this |

### `transactions`
One extracted line item.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `document_id` | FK → `documents.id`, indexed | |
| `provider` | TEXT | Raw payee/description string, unnormalized |
| `category` | enum (16 values) | Set at ingestion via `normalize_category(category_hint)` |
| `transaction_type` | enum | DEBIT / CREDIT / TRANSFER |
| `account_id` | INT, nullable FK | Inherited from `Document.account_id` by `classify_transaction`, unless already set |
| `merchant_id` | INT, nullable FK → `merchants.id` | Set by `classify_transaction` |
| `commitment_id` | INT, nullable FK → `commitments.id` | Set only by the explicit "create Commitment from recurring candidate" action |
| `debt_id` | INT, nullable FK → `debts.id` | Set only by the explicit "link Debt" action |
| `nature` | enum, nullable | ESSENTIAL / DISCRETIONARY — set from `Merchant.default_nature`, unless already set |
| `amount` | FLOAT | Always the positive magnitude; direction lives in `transaction_type`, never the sign |
| `currency` | TEXT | Default EUR |
| `due_date`, `paid_date` | DATE, nullable | |
| `statement_period` | TEXT, nullable | `"YYYY-MM"` |
| `debt_candidate_reviewed` | bool, nullable | `None`/`False` = not yet reviewed; set True on dismiss or link |

### `accounts`
A real bank account or wallet.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `name`, `institution` | TEXT | e.g. "Santander Current Account" / "Santander Totta" |
| `currency` | TEXT | Default EUR — one currency per Account row even for a multi-currency provider (see Revolut note in SYSADMIN) |
| `account_type` | enum | CHECKING / SAVINGS / CARD / WALLET |
| `identifier` | TEXT, nullable | IBAN or last-4, human identification only, never parsed |

### `merchants`
The canonical identity a raw `provider` string resolves to.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `canonical_name` | TEXT | LLM-proposed clean name, e.g. "Modelo Hiper" |
| `default_category`, `default_nature` | enum | Applied to every transaction resolving to this merchant |
| `normalized_key` | TEXT, unique index | Output of `normalize_provider()` — the rules-tier lookup key |
| `confirmed` | bool | False until a human reviews a newly-LLM-created merchant |
| `recurring_reviewed` | bool | False until a human reviews/dismisses a recurring-candidate flag |

### `people`
Lightweight counterparty for informal debt (name, notes) — exists so multiple loans to/from the same person roll up onto one record.

### `commitments`
Planned recurring or yearly-cadence spend.

| Column | Type | Notes |
|---|---|---|
| `cadence` | enum | MONTHLY / QUARTERLY / YEARLY / IRREGULAR |
| `planned_amount` | FLOAT | |
| `year` | INT, nullable | Set **only** for YEARLY cadence — each year is its own row (e.g. "IMI 2026" and "IMI 2027" never share a row), so multi-installment yearly commitments (IMI paid 3-4×/year) accrue correctly via multiple `Transaction.commitment_id` links to the same row |
| `next_due_date` | DATE, nullable | |

### `debts`
Formal (mortgage-style) or informal (person-to-person), one shape for both.

| Column | Type | Notes |
|---|---|---|
| `kind` | enum | FORMAL / INFORMAL |
| `person_id` | nullable FK → `people.id` | INFORMAL only |
| `direction` | enum, nullable | OWED_TO_US / OWED_BY_US — INFORMAL only |
| `original_amount` | FLOAT | Write-once, float is fine |
| `current_balance` | **NUMERIC(12,2)**, not float | A running accumulator — `Transaction.amount`-style float would compound rounding error over hundreds of payments; this was a deliberate fix (see spec) |
| `interest_rate` | FLOAT, nullable | |
| `commitment_id` | nullable FK → `commitments.id` | FORMAL debt with a recurring schedule (e.g. the mortgage) links here; an ad-hoc informal loan has no schedule and links via `Transaction.debt_id` directly instead |

### `utility_readings`
Consumption + cost-breakdown detail beyond the generic Transaction, one row per billed month per utility. Deliberately a separate table from `transactions` so Water/Telecom can reuse it without bloating Transaction for every non-utility category.

### `todos`, `wiki_pages`, `wiki_changes`
Straightforward — see model files. `WikiPage.topic` is unique; a page is upserted (not replaced) so its change history accumulates in `wiki_changes`.

### `alembic_version`
Standard Alembic bookkeeping, single row, current head at time of writing: see `alembic/versions/` for the latest filename.

---

## Design Decisions

**No SQLModel `Relationship` declarations anywhere.** Every cross-table reference (`Transaction.merchant_id`, `.account_id`, etc.) is a plain FK column, resolved via explicit `{id: value}` lookup dicts built in the router when a template needs a related row's display name (see `_lookup_dicts_for` in `transactions.py`). This was a deliberate choice made when the Transactions list needed to show merchant/account *names* rather than raw IDs: since no model in this codebase had ever used `Relationship`, the fix followed that established convention rather than introducing ORM relationships for the first time in one isolated spot.

**Category and Nature are orthogonal, not a replacement.** `Category` (16-value enum, existed from the start) answers "what kind of spend" (groceries, insurance, …). `Nature` (essential/discretionary, added later) answers "is this optional," independent of category — a transaction keeps both. Nature is set from a *merchant's* default, not derived from Category, which means it's a property of *who* got paid, not *what* they sell — the tradeoff being that income transactions also get an (arguably meaningless) essential/discretionary tag, since the LLM assigns Nature per-merchant regardless of `transaction_type`. Flagged as a known artifact for whatever consumes Nature next (e.g. an Overview screen should filter to debits before using it).

**Debt has two independent linkage paths, not one.** `Debt.commitment_id` (a scheduled payment, e.g. the mortgage) and `Transaction.debt_id` (a direct, ad-hoc link, e.g. an informal loan repayment with no fixed schedule) are separate mechanisms because "has a schedule" and "doesn't" are genuinely different repayment patterns — forcing both through one mechanism would mean either inventing a fake Commitment for every ad-hoc loan, or losing the scheduled-payment tracking the mortgage actually needs.

**`classify_transaction()` is the single entry point for classification**, called identically by the live ingestion pipeline (`pipeline.py`) and every one-off backfill script — never re-implemented. This was an explicit design goal from the start (see the classification-engine spec) specifically so the two paths can never silently diverge.

**Nothing in the classification engine ever auto-creates a `Commitment` or `Debt`.** `detect_recurring_candidates()` and `detect_debt_candidates()` only ever return candidates for the Needs Review queue; turning a candidate into a real financial record is always an explicit, human-initiated POST (`create_commitment_from_merchant`, `link_debt`). This holds even at production scale — verified directly against the real database after the historical backfill: zero commitments/debts exist despite 17+ recurring candidates and 16+ debt candidates having been detected.

**Merchant resolution is hybrid (rules first, LLM fallback), not LLM-only.** `normalize_provider()` is a small, fast, free regex pass (strips trailing store codes and known location words); only a rules-tier miss triggers an LLM call via `resolve_merchant_via_llm()`, which then creates a `Merchant` row so every future occurrence of that same normalized string resolves via the free rules tier from then on. This is what keeps a full historical backfill's API cost bounded to roughly the number of *distinct* merchants, not the number of transactions (1,164 merchants vs. 4,848 transactions on this real dataset, after two dedup passes) — but the rules tier's coverage is necessarily incomplete: two production dedup runs so far (post-Santander-backfill, then post-Revolut-import) have each turned up a fresh batch of exact-canonical-name duplicates from provider-string formats the regex didn't catch (503 then 227 merchant rows merged — see `scripts/merge_duplicate_merchants.py`), a known, accepted limitation rather than a bug — expected to recur whenever a new account/institution is imported, since each institution has its own provider-string quirks.

**Statement ingestion commits per line item, not once at the end — with a compensating-delete safety net.** `_ingest_statement` needs each `Transaction` to exist as its own row before `classify_transaction` can act on it without one item's classification failure rolling back its siblings; this was changed from an original single-final-commit design, which broke a pre-existing "no partial ingestion on failure" regression test. The fix tracks every `Transaction` (and any brand-new `Merchant`) created during a failed attempt and deletes them if a later line item aborts the whole statement — restoring the original atomicity guarantee without giving up per-item resilience. (A final-review pass later noted `classify_transaction` never actually reads `transaction.id`, meaning a `session.flush()`-based design might have avoided needing this mechanism at all — left as a documented simplification opportunity, not acted on, since refactoring the highest-regression-risk code path in the system without a fresh review cycle wasn't worth the risk once the compensating-delete version was already verified correct.)

**Migrations are additive-only by policy**, not just by accident: every sub-project's plan explicitly forbids column removals or enum-value changes, and every new `Transaction`/`Document` column has shipped nullable specifically so historical rows never need a backfill *at the same time* as the schema change — backfilling is always a separate, later, explicitly-run step.

---

## Tech Stack

- **Backend**: FastAPI, SQLModel (SQLAlchemy + Pydantic), Alembic, Python 3.12+
- **Database**: SQLite, WAL not explicitly configured (single-process app, low write concurrency)
- **LLM**: Anthropic Claude — Sonnet 5 for document extraction and statement parsing, Haiku 4.5 for cheap classification calls (document type, merchant resolution, wiki-worthiness)
- **Frontend**: Server-rendered Jinja2 + htmx (the only vendored JS dependency) for partial-swap interactivity; no build step, no SPA framework
- **Auth**: Cloudflare Access (JWT verification via JWKS, `app/auth.py`) — the app itself has no login system
- **Testing**: pytest, fixtures build schema from `SQLModel.metadata` directly (not via Alembic) for speed; migrations are verified separately by hand
- **Deployment**: GitHub Actions → SSH → systemd — see `docs/SYSADMIN.md`
