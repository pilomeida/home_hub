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
  ├── GET/POST /financials/bills/*        ── app/routers/bills.py        (upload, list, detail — the ingestion entry point)
  ├── GET/POST /financials/transactions/* ── app/routers/transactions.py (browse/filter/bulk-edit, Needs Review queue)
  ├── GET  /financials/utilities/{tab}    ── app/routers/utilities.py    (Electricity/Water/Telecom consumption + charts)
  ├── GET/POST /house/*                  ── app/routers/house.py        (House documents, items, backlog)
  ├── GET  /wiki/*                 ── app/routers/wiki.py         (auto-maintained standing-facts pages)
  ├── GET  /wiki/lint*             ── app/routers/wiki_lint.py    (Wiki Lint review: dismiss/fixed/add-link; included before wiki.router)
  ├── GET/POST /ask*                ── app/routers/ask.py          (cross-domain Ask chat: conversations, turns, save-to-wiki)
  ├── GET/POST /todos/*            ── app/routers/todos.py        (due-date-driven task list)
  ├── GET/POST /inbox/*            ── app/routers/inbox.py        (shared cross-domain Inbox — review, approve, discard)
  │
  └── Static: /static/*  (htmx.min.js; app/static/documents/ — uploaded source PDFs)

Ingestion channels (separate OS processes, systemd user units):
  home-hub-mailpoll.timer → python -m app.channels.email_poller  (IMAP, every 10 min)
  home-hub-telegram        → python -m app.channels.telegram_bot  (long-polling bot)
        │  ingestion.IncomingFile + caption/subject
        ▼
  inbox_service.receive_document → ingestion.receive_file(initial_status=PENDING_REVIEW)  (domain NULL)
                                 → domain_classifier.suggest_domain_and_category (Haiku, registry-driven)
                                 → InboxItem (suggestion + provenance)
  /inbox Approve → ingestion.validate_classification (errors shown in the form)
                 → wiki log REVIEW "Approved …" → ingestion.finalize_document
                   (domain handler + wiki Ingest — the same core a manual upload uses)
  /inbox Discard → status DISCARDED (file kept) → wiki log REVIEW "Discarded …"
  RECORD categories: finalize_document creates the Record and attaches the file (no Inbox-specific path).
  Later corrections: Plan A's re-filing (/documents/{id}/edit, /records/{id}/edit), not the Inbox.

Ingestion core (app/services/ingestion.py — the ONLY way a file becomes a Document):

  receive_file (content-hash dedup → store → UNCLASSIFIED Document, domain=NULL)
        │            (a reviewing channel — Plan B's Inbox — holds the document here)
        ▼
  finalize_document (validate domain + category + fields against the registry
        │            → tag the Document → dispatch)
        ├─ Financials → FinancialsHandler (app/domains/financials/handler.py):
        │     classify (if no category) → bill: extract_bill → Transaction → classify_transaction
        │                                   → utility detail → todo → ingest_into_wiki
        │                               statement: extract_statement_transactions → Transactions
        └─ House      → HouseHandler (app/domains/house/handler.py):
              warranty without a date → extract_warranty_expiry (Haiku, best effort)
              → renewal To-Do at expiry − 21 days → ingest_into_wiki (item page, no LLM)

Knowledge layer (app/services/wiki_engine.py decides WHAT, app/services/wiki_store.py HOW):
  ingest_into_wiki(document) → claims_from_fields (registry WikiSchema entity types)
                             + assess_document_for_wiki (LLM, only if the domain gives guidance)
                             → apply_claims: pages ← claims ← source documents;
                               changed claims SUPERSEDED (never deleted); one wiki_log entry

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
Config: app/config.py ← reads .env at startup (LLMSEL_* gateway settings; DATABASE_PATH,
DOCUMENTS_DIR, CF_ACCESS_* optional).
```

The web app stays **single-process, no queue/worker daemon**. All ingestion (including the multi-call
extraction + classification) runs synchronously inside the request handler for `POST /financials/bills/upload`,
or inside a one-off script's own event loop for backfills. The background ingestion channels
(email poller, Telegram bot) run as their own, separate, crash-isolated systemd **user**-unit
processes and share the same SQLite file — short transactions and the default 5-second busy
timeout are enough given how infrequent channel writes are. WAL is deliberately not enabled (see
the backups section of SYSADMIN): it would silently break the documented `cp home_family.db`
backup habit.

---

## Repository Layout

```
Home & Family/
├── app/
│   ├── main.py                FastAPI app init; CORS-free (Cloudflare Access gates access);
│   │                           registers CloudflareAccessMiddleware + all routers; mounts /static
│   ├── config.py               Settings from .env (LLMSEL_URL/WORKER/TOKEN, DATABASE_PATH, DOCUMENTS_DIR,
│   │                           CF_ACCESS_TEAM_DOMAIN, CF_ACCESS_AUD)
│   ├── db.py                   SQLModel engine + get_session() FastAPI dependency; registers the
│   │                           PRAGMA foreign_keys=ON connect-event listener
│   ├── auth.py                 CloudflareAccessMiddleware — verifies the CF Access JWT via JWKS,
│   │                           attaches request.state.user_email; skips only /health
│   ├── templating.py           Shared Jinja2Templates instance (`app.templating.templates`) —
│   │                           domain routers use this instead of building their own
│   │
│   ├── models/                 One file per entity; app/models/__init__.py imports every module so
│   │   │                       SQLModel.metadata is fully populated (required for create_all/alembic)
│   │   ├── document.py         Document: a source file (manual/email/api/telegram), status lifecycle
│   │   │                       PENDING → PROCESSED | NEEDS_ATTENTION, plus channel intake's
│   │   │                       PENDING_REVIEW → (finalized) | DISCARDED; account_id (which bank
│   │   │                       account this statement belongs to)
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
│   │   ├── todo.py             Todo: title, due_date, done, domain, optional transaction_id /
│   │   │                       document_id / record_id
│   │   ├── record.py           Record: a hand-entered source (domain, category, fields_json,
│   │   │                       optional attached document_id, entered_by, retired_at)
│   │   ├── inbox_item.py       InboxItem: channel provenance + the classifier's suggestion + review
│   │   │                       audit for a Document arriving through an ingestion channel — never
│   │   │                       duplicates Document.status; suggested_domain is a plain string
│   │   └── wiki.py             WikiPage (page_type + entity_key, facts_json = active-claims cache),
│   │                           WikiClaim (+ note), WikiClaimSource (document OR record, +
│   │                           withdrawn_at), WikiLogEntry (+ record_id), WikiLink, WikiChange (legacy)
│   │
│   ├── domains/                Domain registry — the ONLY extension point (see Design Decisions
│   │   │                       and "Adding a domain"); shared code never names a domain
│   │   ├── base.py             Domain enum, DomainSpec / DomainHandler / CategorySpec / FieldSpec /
│   │   │                       WikiSchema dataclasses; SourceKind (DOCUMENT | RECORD, on CategorySpec)
│   │   ├── registry.py         get_spec / all_specs — loads SPEC from _SPEC_MODULES, validates
│   │   │                       domain uniqueness; document_url / record_url
│   │   ├── fields.py           FieldInput helpers shared by the domain field-inputs template
│   │   ├── entries.py          domain_entries / SourceEntry / record_for_document — the uniform
│   │   │                       document-or-record listing (see Documents vs Records)
│   │   ├── financials/         categories.py, handler.py (FinancialsHandler — refile_blocker +
│   │   │                       withdraw enforce the Financials re-filing rule), overview.py
│   │   │                       (overview_card), spec.py (SPEC), ask.py (FINANCIALS_ASK_TOOLS —
│   │   │                       spending + find_transactions, the domain's Ask plug-in)
│   │   └── house/              Same shape + items.py (item-card aggregation) and warranty.py
│   │                           (extract_warranty_expiry, effective_warranty_expiry/derive_fields,
│   │                           the −21-day renewal To-Do)
│   │
│   ├── channels/                Ingestion channel adapters — each turns an external message into
│   │   │                       ingestion.IncomingFile and calls inbox_service.receive_document,
│   │   │                       nothing else; each runs as its own OS process (see deploy/)
│   │   ├── email_parsing.py     Pure parsing of one raw RFC822 email into its usable attachments
│   │   ├── email_poller.py      Mailbox protocol + ImapMailbox; poll_once() — moves handled mail
│   │   │                       into Hub-Processed / Hub-Ignored / Hub-Failed; run by
│   │   │                       home-hub-mailpoll.timer
│   │   └── telegram_bot.py      The Hub's own dedicated Telegram bot (long-polling); run by
│   │                           home-hub-telegram.service
│   │
│   ├── routers/                One file per nav section; each owns its own Jinja2Templates instance
│   │   ├── dashboard.py        `/` and `/health`
│   │   ├── bills.py            `/financials/bills*` — upload form + POST (dedup by content_hash, then
│   │   │                       the ingestion core), list, per-document detail
│   │   ├── house.py            `/house*` — landing, Add a document (fields swap per category), item
│   │   │                       cards, detail, edit fields, backlog — via the ingestion core
│   │   ├── documents.py        `/documents/{id}/edit`, `/records/{id}/edit` — the generic,
│   │   │                       domain-agnostic Edit / re-file screens (change domain, category and
│   │   │                       fields for any finalized Document or Record, via refile_document /
│   │   │                       refile_record); redirects a Document with an attached Record to the
│   │   │                       Record's edit screen
│   │   ├── transactions.py     `/financials/transactions*` — filtered/paginated list, bulk-edit, Needs Review
│   │   │                       tab (confirm/dismiss/create-commitment/link-debt actions)
│   │   ├── utilities.py        `/financials/utilities/{tab}` — Electricity/Water/Telecom table + bar charts
│   │   ├── wiki.py             `/wiki*` — page list + detail with change history, `/wiki/log`
│   │   ├── wiki_lint.py         `/wiki/lint*` — Wiki Lint review: run now, dismiss/fixed/add-link;
│   │   │                       included in main.py BEFORE wiki.router (`/wiki/{page_id}` would
│   │   │                       otherwise 422 on the literal path `/wiki/lint`)
│   │   ├── ask.py               `/ask*` — chat home (new chat + recent conversations), one
│   │   │                       conversation, POST a follow-up turn, GET a turn partial (htmx
│   │   │                       polling), POST save-to-wiki
│   │   ├── todos.py            `/todos*` — open/done lists, mark-done (htmx partial swap)
│   │   └── inbox.py            `/inbox*` — the shared, cross-domain Inbox: list pending items
│   │                           (with domain field inputs pre-filled when confident), the fields
│   │                           partial (Area/Type change), approve, discard
│   │
│   ├── services/                Business logic, no HTTP/template concerns
│   │   ├── ingestion.py         receive_file / finalize_document — the ingestion core: the ONLY
│   │   │                       way a file becomes a Document (see diagram above); also
│   │   │                       create_record / attach_file / update_record_fields (the only way
│   │   │                       into a RECORD category) and refile_document / refile_record (see
│   │   │                       Re-filing)
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
│   │   ├── todo_backlog.py      open_todos_for_domain / open_todo_count / backlog_context —
│   │   │                       the per-domain backlog behind `todos/_backlog.html`
│   │   ├── domain_overview.py   build_domain_cards — per-domain cards on the Overview dashboard
│   │   ├── wiki_engine.py       WHAT goes in the wiki: claims_from_fields (registry WikiSchema
│   │   │                       entity types) + assess_document_for_wiki (LLM, only if the domain
│   │   │                       gives guidance) → ingest_into_wiki
│   │   ├── wiki_store.py        HOW the wiki is stored: find_page / _get_or_create_page,
│   │   │                       apply_claims (pages ← claims ← source documents; changed claims
│   │   │                       superseded, never deleted; one wiki_log entry), active_claims,
│   │   │                       sources_for_claims, recent_log, build_wiki_index
│   │   ├── document_input.py    build_content_block — base64 PDF/image → Claude content block;
│   │   │                       is_model_readable — which files build_content_block can send Claude
│   │   ├── json_utils.py        strip_json_fences — strips a ```json fence if Claude adds one
│   │   ├── storage.py           save_upload — writes to DOCUMENTS_DIR, returns (path, sha256 hash)
│   │   ├── domain_classifier.py suggest_domain_and_category — registry-driven domain+category
│   │   │                       suggestion (Haiku); the prompt is built from each DomainSpec's own
│   │   │                       description, so a new domain needs no classifier changes
│   │   ├── inbox_service.py     receive_document / pending_entries / approve / discard — the only
│   │   │                       entry point any ingestion channel calls; approve is the only place
│   │   │                       a channel document is finalized (validate_classification then
│   │   │                       finalize_document, the same core a manual upload uses)
│   │   ├── ask/                 Plan C's Ask (Query op), wiki-first agentic tool use — read-only
│   │   │   ├── contracts.py     Citable, ToolOutput, AskTool(.to_api()) — the domain extension contract
│   │   │   ├── refs.py          Citables for wiki pages / documents / records / todos; source_ref
│   │   │   ├── tools.py         settled_entries()/is_settled()/is_settled_source() — the ONE place
│   │   │   │                   visibility is decided (PROCESSED documents + non-retired records);
│   │   │   │                   core tools (read_wiki_pages, find_sources, read_document_file,
│   │   │   │                   list_todos) + domain_tools()/available_tools()
│   │   │   ├── context.py       build_system_prompt() — rules + domains + wiki index + pending-review note
│   │   │   ├── citations.py     [[ref]] marker parsing → RenderedAnswer, per turn
│   │   │   ├── conversation.py  start_conversation, add_turn, build_history (bounded, last 6
│   │   │   │                   answered turns), recent_conversations
│   │   │   ├── engine.py        run_turn() — the bounded Claude tool-use loop for one turn
│   │   │   └── save.py          save_answer_as_wiki_page() — one turn → an `answer` wiki page via
│   │   │                       apply_claims + add_link
│   │   └── wiki_lint/            Plan C's Wiki Lint — never edits facts
│   │       ├── findings.py      FindingDraft (+ fingerprint()) — identity independent of wording/order
│   │       ├── checks.py        Deterministic checks: orphan pages, unsourced claims, missing links,
│   │       │                   stale links, uningested sources, stale saved answers
│   │       ├── llm_checks.py    One Claude audit per domain (contradictions/stale claims/gaps),
│   │       │                   guided by that domain's `spec.wiki`; a claim's NOTE marks a
│   │       │                   deliberate assumption, never reported as a gap on its own
│   │       └── runner.py        start_run/execute_run/run_lint, persist_findings — sticky
│   │                           dismissals, deterministic findings auto-resolve, LLM findings don't
│   │
│   ├── jobs/
│   │   └── wiki_lint.py         `python -m app.jobs.wiki_lint` — the weekly Wiki Lint pass, run by
│   │                           `deploy/systemd/home-hub-wikilint.timer`
│   │
│   ├── templates/               Jinja2, server-rendered, htmx for partial-swap interactivity
│   │   ├── base.html            Nav shell; loads htmx.min.js; shared CSS
│   │   ├── dashboard.html
│   │   ├── dashboard/           Overview partials (_kpi_card, _domain_cards, _household,
│   │   │                       _needs_attention, _period_panel, _yearly_commitments_card)
│   │   ├── bills/{list,upload,detail}.html
│   │   ├── house/{landing,upload,detail}.html + _item_card.html
│   │   ├── domains/_field_inputs.html   Shared per-domain metadata field inputs (htmx swap)
│   │   ├── transactions/
│   │   │   ├── list.html                 Filters (GET form) + bulk-edit (htmx POST form, paginated)
│   │   │   ├── _rows.html                 Table body partial (swapped in after bulk-edit)
│   │   │   ├── needs_review.html
│   │   │   └── _needs_review_rows.html    4 sections: unconfirmed merchants, recurring candidates
│   │   │                                 (+ create-Commitment form), debt candidates (+ link-Debt
│   │   │                                 form), unclassified transactions (read-only)
│   │   ├── utilities/tab.html
│   │   ├── wiki/{list,page,log}.html + lint.html, _finding.html (Wiki Lint review page)
│   │   ├── ask/home.html, conversation.html, _turn.html   home = new-chat box + 20 most recent
│   │   │                                                  conversations; conversation = full chat +
│   │   │                                                  follow-up box; _turn = one question/answer
│   │   │                                                  (htmx-polled while pending; save-to-wiki form)
│   │   ├── todos/{list,_lists}.html + _backlog.html (shared per-domain backlog partial, `id="todo-{id}"`
│   │   │                                            anchors so Ask's to-do citations can link into it)
│   │   └── inbox/{list,_entry,_fields,_result}.html   list = page shell + "Recently handled" table;
│   │                                                  _entry = one pending card (form); _fields =
│   │                                                  Area/Type selects + domains/_field_inputs.html;
│   │                                                  _result = card replacement after approve/discard
│   │
│   └── static/
│       ├── htmx.min.js          The app's only vendored JS dependency
│       └── documents/           Uploaded/imported source files (gitignored; content-hash-named)
│
├── alembic/
│   ├── env.py                   render_as_batch=True (required for SQLite ALTER TABLE); reads
│   │                           settings.database_url; imports app.models so metadata is complete
│   └── versions/                18 linear migrations, no branches — see Database Schema below
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
│   ├── domain_fakes.py          Stand-in DomainSpec/DomainHandler fakes shared by tests that
│   │                           exercise the registry without touching the real domains
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
├── deploy/
│   ├── systemd/                    Unit files installed as systemd USER units of the home-hub
│   │                               account (no sudo) — the convention every future background job
│   │                               (bank sync included) follows: *.timer -> enabled+started;
│   │                               *.service with [Install] -> long-running, enabled+restarted on
│   │                               every deploy; *.service without [Install] -> timer-driven
│   │                               oneshot, installed only
│   └── install_user_units.sh       Installs/refreshes the units above; run by deploy.yml
│
├── .github/workflows/deploy.yml   CI/CD — see docs/SYSADMIN.md
├── alembic.ini
├── pyproject.toml                 pytest config only
├── requirements.txt
└── data/home_family.db            The real SQLite database (gitignored)
```

---

## Database Schema

Single file: `data/home_family.db` (SQLite). Additive-only migration history (20 revisions, one
linear chain, no branches, head `e7b3d1f4a6c8`) — every schema change to date has been a new table
or a new nullable column; nothing has ever been dropped or had an existing column's meaning changed.

### `documents`
A source file (uploaded, forwarded, or synced). `domain` set ⇔ finalized: a Document whose
`domain` is NULL is UNCLASSIFIED and waiting in a reviewing channel (Plan B's Inbox).

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `filename`, `file_path` | TEXT | `file_path` is under `app/static/documents/`, content-hash-named |
| `content_hash` | TEXT, indexed | SHA-256 of the file bytes — upload-time dedup key |
| `source` | enum | MANUAL / EMAIL / API / TELEGRAM |
| `status` | enum | PENDING → PROCESSED \| NEEDS_ATTENTION, or channel intake's PENDING_REVIEW → (finalized) \| DISCARDED |
| `domain` | enum, nullable | FINANCIALS \| HOUSE — set at `finalize_document`; NULL ⇒ UNCLASSIFIED |
| `category` | TEXT, nullable, indexed | The domain's category value (validated against the registry at finalize); Financials' former `doc_type` |
| `fields_json` | TEXT | JSON object of string values — per-domain metadata, validated against the registry's `FieldSpec`s before write |
| `doc_type` | TEXT, nullable | **Deprecated** — copied into `category` by 3b7e9c1d2f40, no longer read or written |
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

### `todos`
Task list. Straightforward columns (title, due_date, done) plus optional FKs: `transaction_id`
(financials todo from a bill), `document_id` (nullable FK → `documents.id`, e.g. a House
renewal To-Do derived from a warranty document) and `record_id` (nullable FK → `records.id`,
indexed — a To-Do derived from a hand-entered record). Every Todo belongs to a `domain`, which is
what the per-domain backlogs (`app/services/todo_backlog.py`, `todos/_backlog.html`) filter on.

### `records`
A hand-entered source — the Record counterpart to Document (see *Documents vs Records* below).

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `domain` | enum, indexed | |
| `category` | TEXT, indexed | A `SourceKind.RECORD` category value from the same per-domain registry as Document categories |
| `fields_json` | TEXT | Same shape and validation as `Document.fields_json` |
| `document_id` | INT, nullable FK → `documents.id`, **unique index** | The record's optional attached file (receipt/report) — unique because a Document attaches to at most one Record |
| `entered_by` | TEXT, nullable | CF Access email, or a script name |
| `created_at`, `updated_at` | | |
| `retired_at` | TEXT/DATETIME, nullable | Set when the record is re-filed into a DOCUMENT category (see *Re-filing*); the row is kept, never deleted |

### `inbox_items`
Channel provenance + the classifier's suggestion + a review audit, one row per Document that
arrived through an ingestion channel (email, Telegram). Review state itself lives on
`Document.status` (PENDING_REVIEW → finalized, or DISCARDED); this row never duplicates it.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `document_id` | INT, FK → `documents.id`, **unique index** | One InboxItem per Document |
| `context_text` | TEXT, nullable | Email subject + snippet, or the Telegram caption |
| `external_ref` | TEXT, nullable | `"email:<Message-ID>#<n>"` / `"telegram:<chat>:<msg>"` |
| `suggested_domain` | TEXT, nullable | **A plain string, not a DB enum** — a `Domain` value validated against the registry when written, so a new domain becomes suggestible just by registering, no migration needed |
| `suggested_category` | TEXT, nullable | |
| `confidence` | FLOAT, nullable | 0.0–1.0 as reported by the classifier |
| `classifier_note` | TEXT, nullable | The classifier's one-line reason, or why it couldn't run |
| `received_at` | DATETIME | |
| `reviewed_by`, `reviewed_at` | TEXT / DATETIME, nullable | Set by `approve`/`discard`; NULL while the item is still pending |

### `wiki_pages`, `wiki_claims`, `wiki_claim_sources`, `wiki_links`, `wiki_log`, `wiki_changes`
The knowledge layer. `WikiPage` is keyed by (`page_type`, `entity_key`, unique together) —
`TOPIC` pages (the original standing-facts pages, `entity_key` NULL) plus per-domain item pages
(e.g. House items, where `entity_key` is the normalized item name); it carries `summary` and
`facts_json` — a **cache of the page's ACTIVE claims**, rebuilt only by `apply_claims`.
`WikiClaim` rows are the actual knowledge units (page_id, key, value, superseded_at); a changed
claim is superseded — a new row written, the old one stamped — never deleted or edited. `WikiClaim.note`
(nullable) carries a claim-level annotation such as an "assumed" note (see *Derived fields and claim
notes* below) — a normal field of the claim, not itself versioned.
`WikiClaimSource` links each claim to the source asserting it: **a Document OR a Record, exactly
one** (`document_id` nullable, `record_id` nullable FK → `records.id` indexed, check constraint
`ck_wiki_claim_sources_one_source` enforces exactly one of the two is set). `withdrawn_at`
(nullable) is set when that source is re-filed or edited and no longer asserts the claim, while the
claim itself stays ACTIVE if another source still supports it (see *Re-filing*). `WikiLink` rows are
directed page-to-page cross-links (a bidirectional relation is two rows; unique per direction;
append-only). `WikiLogEntry` is an
append-only log of every `apply_claims` run, tagged with a `WikiOperation`
(ingest / edit / query / lint / review / migration), with `record_id` (nullable FK, alongside the
existing `document_id`) so an entry can point at a hand-entered source; `/wiki/log` shows it.
`wiki_changes` is the legacy WikiChange history table — still read-only, no longer written.

### `ask_conversations`, `ask_turns`
Plan C's Ask chat. `AskConversation` (id, title — the first question, truncated; started_by; created_at;
updated_at, indexed) holds ordered `AskTurn`s (conversation_id + position, unique together; question;
status `pending|answered|failed`; `answer_text` — raw model text incl. `[[ref]]` markers;
`citations_json` — the refs THIS turn cited, `[{"ref","label","url"}]`; `used_raw_sources`; `asked_by`;
`error`; `input_tokens`/`output_tokens`; `saved_wiki_page_id`, nullable FK → `wiki_pages.id`,
set once the answer is saved; `created_at`/`answered_at`). A turn is also the handle for "Save to wiki".

### `lint_runs`, `lint_findings`
Plan C's Wiki Lint. `LintRun` (trigger `timer|manual`; status `running|succeeded|partial|failed`;
started_at/finished_at; new_count/open_count/auto_resolved_count; errors — one line per domain whose
LLM audit failed, joined). `LintFinding` rows persist across runs, keyed by `fingerprint` (indexed) —
what a finding is about, not how it's worded, so identity survives a re-run with different phrasing:
kind (contradiction/stale_claim/gap/orphan_page/missing_link/stale_link/unsourced_claim/
uningested_sources/stale_saved_answer), domain, summary, suggested_action, `wiki_page_ids_json` (order
kept — `[from, to]` for missing/stale link), `claim_ids_json`, `document_ids_json`, `record_ids_json`,
status (`open|dismissed|fixed|auto_resolved`), `first_seen_run_id`/`last_seen_run_id` (FK → `lint_runs`),
resolved_at/resolved_by. A dismissal sticks across runs; an OPEN deterministic finding not seen in a
later run is auto-resolved; LLM findings are never auto-resolved (model output varies between runs).

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

**Domain registry is the only extension point.** Every per-domain behavior — categories, metadata fields, handlers, overview cards, wiki schema — is declared in a `DomainSpec` (`app/domains/<domain>/spec.py`) and reached through `app/domains/registry.py`; shared code (ingestion core, backlogs, overview cards, wiki engine, templating, base template) never names a domain, it looks everything up. A `DomainSpec` declares: the `Domain` member, its `CategorySpec`s (label + which media kinds it applies to + option ordering), its `FieldSpec`s (per-category metadata fields with validation rules), a `WikiSchema` (which entity claims this domain's fields feed), its `DomainHandler` (the `process` / `on_fields_changed` hooks the ingestion core dispatches to), and its `overview_card`. The ingestion core is deliberately split into `receive_file` (dedup → store → UNCLASSIFIED Document with `domain=NULL`) and `finalize_document` (validate domain+category+fields against the registry → tag → dispatch to the handler) so that a reviewing channel — Plan B's Inbox — can hold a document between the two, letting a human classify instead of forcing an eager guess at upload time.

**Domain metadata is `fields_json` validated by `FieldSpec`s, not per-domain tables.** Each domain wants different metadata on its documents (a warranty has an expiry date; a bill has a provider and period), and per-domain tables would mean a new table + migration + query surface per domain forever. Instead every Document carries one generic `fields_json` blob of string values, and the registry's `FieldSpec`s are what make it meaningful — they define which fields exist per category, which are required, and how they validate, so `finalize_document` can refuse an invalid write and templates can render fields generically.

**The wiki is a claim-based knowledge layer** (Karpathy's LLM-Wiki pattern). Raw sources stay as Documents; wiki pages are assembled from *claims* (`wiki_claims`), each traceable to its source documents via `wiki_claim_sources`. Claims are never deleted or edited — a new value supersedes the old one, which stays for provenance. Each page's `facts_json` is a rebuilt cache of its active claims, and every `apply_claims` run appends exactly one `wiki_log` entry, so the wiki's full evolution is auditable. Ingestion of claims lives here (Task 17's knowledge layer); Query and Lint of the wiki are Plan C work. Cross-links between pages are declared per domain via `EntityTypeSpec.links` (`LinkSpec`) — House links item ↔ room — and written through `apply_claims(links=...)`; they are append-only like claims. `ingest_into_wiki` logs every call, so every PROCESSED finalized document has at least one `INGEST` entry. `answer` pages (page_type `answer`) are Plan C's saved Ask answers — no source document, indexed under "Saved answers".

**Documents vs Records.** A `Document` is an immutable raw source: a file plus its provenance
(`file_path`, `content_hash`, `source`) — it is never rewritten, only re-tagged. A `Record` is a
human-authored source: domain, category, `fields_json` and who entered it, with no file of its
own required. The registry decides per category which shape applies: `CategorySpec.kind` is
`SourceKind.DOCUMENT` (the entry IS an uploaded file) or `SourceKind.RECORD` (typed in by hand; a
file *may* be attached — e.g. a maintenance visit with a receipt). A Record's optional attachment
is a normal Document with `Record.document_id` pointing at it (unique — a Document attaches to at
most one Record); the attachment's own `fields_json` stays `{}`, since the Record holds the fields.
**The ingestion core is the only way into a RECORD category** — a file finalized into one, whether
by upload, Plan B approval, or re-filing, becomes a Record's attachment, never a bare Document with
fields. Both kinds are treated uniformly downstream: `app/domains/entries.py`'s `domain_entries()`
returns documents and records in one shape (`SourceEntry`) for listings and cards; the wiki takes
either as a claim source (`WikiClaimSource.record_id`, exactly one of document/record set); derived
to-dos can link to either (`Todo.record_id`).

**Derived fields and claim notes.** Some facts are computed, not stated — Portugal's 3-year legal
guarantee is the first example: when a House warranty document states a purchase date but no
explicit expiry, the app assumes the expiry is the purchase date plus 3 years, and marks it
**assumed**, never as if it were a stated fact. `DomainSpec.derive_fields(category, fields)` is the
per-domain hook that computes such fields (House's `house_derived_fields` /
`effective_warranty_expiry`) from whatever the user or extractor actually provided; it never writes
to `fields_json` itself. The wiki carries the distinction via `WikiClaim.note` (e.g. "Assumed:
Portugal's 3-year legal guarantee from the purchase date (…)"), populated through
`FactSpec.note_field`. Entering a real expiry date later removes the assumption automatically — the
derived value disappears once a stated one exists, and the new claim supersedes the assumed one.

**Re-filing: withdraw, then file again through the same core path.** Any finalized Document or
Record can be re-filed — its domain, category and fields can all change — via `refile_document` /
`refile_record`, which withdraw the source from its old classification and then run it through the
same `_file_under` path a fresh upload uses, so there is exactly one way a source ends up filed
anywhere.
- The wiki withdrawal (`withdraw_from_wiki`) *supersedes* the source's claims; it never deletes
  them. A claim that other sources still support stays ACTIVE — only this source's
  `WikiClaimSource` link is marked `withdrawn_at`. The withdrawal is logged as `EDIT`.
- Open auto-generated to-dos linked to the source are derived data: `_drop_open_derived_todos`
  deletes them and the new handler re-derives what's still applicable. Done to-dos are history and
  are kept.
- The old domain's handler reverses its own derived data via `DomainHandler.withdraw()`. It can
  refuse via `refile_blocker()` when reversing would destroy a human decision — checked, and
  raising `RefileRefusedError`, *before* any mutation happens.
- **Financials rule:** re-filing a Financials document deletes its derived Transactions,
  UtilityReadings and their open to-dos. Merchants are shared and are kept. Re-filing is refused,
  with an explanation, if any of its Transactions carries a human-made link: a Commitment, a Debt,
  or a transfer pair (`linked_transaction_id` in either direction). Automated derivations are
  recomputable; human decisions are never discarded silently.
- A Record re-filed into a DOCUMENT category is *retired* (`Record.retired_at` set), not deleted —
  its attached Document is re-filed in its place and becomes a normal, unattached Document.
- Implementation note: `create_record`/`refile_record` check the target category's `kind` via a
  private `_target_kind()` helper *before* `validate_classification` runs, so they can raise their
  own specific error ("filed from a document, not entered by hand" / "can only become a document if
  it has a file attached") ahead of `validate_classification`'s generic "choose a file" error for a
  DOCUMENT-kind category with no file — a coordinator ruling made during Task 22 (see the build log),
  since `validate_classification` itself is correct as written for the upload/Inbox path and was
  left unchanged.

**The Inbox's suggestion lives in `inbox_items`, never on `Document.domain`.** The invariant
`Document.domain IS NOT NULL ⇔ finalized` (see *Domain registry is the only extension point* above)
has to hold for every document, channel-sourced ones included: a channel document arrives with
`domain=NULL` and `status=PENDING_REVIEW`, and the classifier's best guess (`suggested_domain`,
a plain string, not a DB enum — see the `inbox_items` schema note) is recorded on a separate
`InboxItem` row instead of being written to `Document.domain`. This keeps "a suggestion" and "a
finalized classification" from ever being confused, and means a stale suggestion (e.g. a domain
later deregistered) simply fails to resolve rather than corrupting the Document itself. `approve()`
is the only place a channel document is ever finalized — it calls the same `validate_classification`
→ `finalize_document` core a manual upload uses.

**Ingestion channels run as systemd *user* units, not a broader sudo rule.** The email poller and
Telegram bot are separate, crash-isolated OS processes (per the 2026-09-14 rule that background
work never runs inside the web request/response cycle), deployed as systemd units of the `home-hub`
account rather than as root-managed system units. A unit file installed under sudo *could* run as
root (e.g. via `ExecStartPre=+…`), which would make the deploy SSH key root-equivalent; user units
avoid that risk entirely, at the one-time cost of `loginctl enable-linger home-hub` so those user
units keep running without an interactive login session. `deploy/install_user_units.sh` installs
and refreshes them on every deploy — no `systemctl`/`sudo` call in the deploy pipeline needs root.

**Ask is the knowledge layer's Query operation, as a chat.**
- Each turn is wiki-first, agentic tool use over live tables (no embeddings or search index), with
  the last 6 answered turns replayed as plain text.
- Domains extend it only via `DomainSpec.ask_tools`.
- Only filed PROCESSED documents and non-retired records are visible (`settled_entries()`), and
  withdrawn source links never support a claim (`claim_sources()`).
- Claim notes (e.g. an assumed warranty date) are shown to the model and must be stated in the answer.
- Citations are validated per turn.
- Saved answers go through `apply_claims(..., operation=QUERY)` as `ANSWER_PAGE_TYPE` pages.

**Lint never edits facts.**
- Findings persist by fingerprint, dismissals stick, deterministic findings auto-resolve, LLM
  findings wait for a human.
- Stale links (append-only links whose target no longer matches the newest document) are flagged,
  not removed.
- The only fix it offers is a human-clicked Add link (logged as EDIT).

**Documents are reconciled to the bank.** The bank statements (uploaded statements and the automatic bank sync) are the source of truth for money that moved. A bill, invoice or receipt makes a Transaction row that is only the *document's* record of a payment; its date is the bill's, not the day the money left. `services/document_reconcile.py` matches each such row to the bank row that paid it (same amount, a payee word in common, a bounded date window, one bank row per document, oldest document first) and sets `Transaction.settled_by_id` to it. A settled row is shown dimmed with a link to the bank entry, the bank entry shows the document as an attachment, and **every money aggregate filters `settled_by_id IS NULL`** (Overview flows and cash balance, budgets and forecast, tags, Ask, the Needs Review queues). It runs automatically after a bill, a statement or a bank sync is ingested (`reconcile_quietly`), and `scripts/reconcile_documents.py` handles a backlog. Anything that does not match stays counted and is listed for a human. Withdrawing a bank document reopens the bills it had settled. New money aggregates must add the same filter.

**Adding a domain** (checklist for Health / Education / Vehicles / Legal):

1. Add the `Domain` member. `documents`, `todos` and `wiki_pages` store `domain` as `VARCHAR(10)` with no CHECK constraint, so a member whose name fits in 10 characters (`INSURANCE` was added this way) needs **no** migration; a longer name, or a database that has gained a CHECK, needs one (copy `3b7e9c1d2f40`'s pattern).
2. Create `app/domains/<domain>/` with `categories.py`, `handler.py` (a `DomainHandler`), `overview.py` (`overview_card`), and `spec.py` (`SPEC`, including its `fields` and `wiki` schema).
3. Append `"app.domains.<domain>.spec"` to `_SPEC_MODULES` in `app/domains/registry.py`.
4. Create `app/routers/<domain>.py` (+ `app/templates/<domain>/`) using `app.templating.templates`, the ingestion core (`receive_file` / `finalize_document`) for uploads, `domains/_field_inputs.html` for metadata, and `todos/_backlog.html` for the backlog; include the router in `app/main.py`.
5. For any category that's hand-entered rather than filed from a file (a mileage log, a doctor
   visit), declare `kind=SourceKind.RECORD` on its `CategorySpec` and set `DomainSpec.record_url`
   (where a Record of this domain is viewed — parallel to `document_url`). Optionally set
   `DomainSpec.derive_fields` for any fact that should be computed rather than stated (see *Derived
   fields and claim notes*).
6. Nothing else changes: nav, Overview card row, backlogs, the wiki index, the generic
   `/documents/{id}/edit` and `/records/{id}/edit` re-filing screens, and (once they exist) Plan B's
   Inbox classifier and Plan C's Ask all pick the domain up from the registry.

**Known follow-up:** Financials-only services still live in `app/services/` — `extraction.py`, `categorization.py`, `classification_engine.py`, and `find_duplicate_transaction` / `generate_todo_for_transaction` in their current files. They are not shared code; they are called only by the Financials handler and Financials routers. Moving them under `app/domains/financials/` is a pure relocation reserved for a later cleanup.

---

## Tech Stack

- **Backend**: FastAPI, SQLModel (SQLAlchemy + Pydantic), Alembic, Python 3.12+
- **Database**: SQLite, WAL not explicitly configured (single-process app, low write concurrency)
- **LLM**: Anthropic Claude — Sonnet 5 for document extraction and statement parsing, Haiku 4.5 for cheap classification calls (document type, merchant resolution, wiki-worthiness)
- **Frontend**: Server-rendered Jinja2 + htmx (the only vendored JS dependency) for partial-swap interactivity; no build step, no SPA framework
- **Auth**: Cloudflare Access (JWT verification via JWKS, `app/auth.py`) — the app itself has no login system
- **Testing**: pytest, fixtures build schema from `SQLModel.metadata` directly (not via Alembic) for speed; migrations are verified separately by hand
- **Deployment**: GitHub Actions → SSH → systemd — see `docs/SYSADMIN.md`
