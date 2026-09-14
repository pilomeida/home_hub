# Financials Tab Move Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the existing Bills/Transactions/Utilities screens under a `/financials/*` URL prefix and a visually grouped "Financials" nav section, and generalize the shared `Document`/`Todo`/`WikiPage` layer with a `domain` tag — the foundation the House/Health/Education/Vehicles/Legal & Identity tabs (each its own future plan) and bank auto-capture will build on.

**Architecture:** Add a `Domain` enum (currently just `FINANCIALS`) and a nullable `domain` column to `Document`, `Todo`, and `WikiPage`, backfilling existing rows to `FINANCIALS` and setting it explicitly at every creation call site. Rename the three existing routers' prefixes to `/financials/bills`, `/financials/transactions`, `/financials/utilities`, updating every template, service, and test that references the old paths. Restructure the sidebar nav to visually group those three under a "Financials" label while Wiki and Todos stay top-level (per brainstorm Q3). The multi-domain Overview card grid and per-page Financials subnav are explicitly deferred to when a second domain tab exists — see "Out of scope" below.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy, Alembic (SQLite, `render_as_batch=True`), Jinja2 + htmx, pytest.

**Spec:** `/home/pedro/Desktop/Claude_Corner/brainstorms/2026-09-14-home-hub-tabs-restructure.md` — Part 1 (Q1–Q12). This plan implements only the "Financials move" checkpoint from Q10; the 5 new domain tabs and bank auto-capture (Part 2) are separate future plans. (Absolute path: this file lives outside the git repo, in the user's personal brainstorms folder, and a relative link breaks depending on worktree nesting depth.)

## Global Constraints

- Migrations are additive-only: new columns ship `nullable=True`, historical rows get an explicit backfill `UPDATE` in the same migration, never a blanket `server_default` (see `docs/ARCHITECTURE.md` § Design Decisions, and `doc_type`'s own precedent in `21636872a4f1_add_doc_type_to_documents.py`).
- Enum columns follow the existing `sa.Enum(...)` pattern (see `ab1070af8e0c_add_documents_table.py`, `7ce9ad97895f_...`) — not a plain `sa.String()` — because `domain` values are code-controlled, not LLM-derived (unlike `doc_type`).
- `classify_transaction()` and every other shared pipeline function stay the single entry point regardless of how a record arrived — this plan does not touch that pipeline, only the URL surface and the shared tagging layer.
- Test migrations against a scratch copy of `data/home_family.db`, never the real file (`docs/SYSADMIN.md` § 3).
- No secrets, no `.env` values, committed anywhere in this plan's changes.

## Out of scope (explicitly deferred, not forgotten)

- **Multi-domain Overview card grid** (brainstorm Q8): `/` keeps showing exactly what it shows today (Financials KPIs + Household panel). Building a card-per-domain grid now, with only one real domain to show, would be scaffolding without content. Revisit when the first new domain tab (House) ships.
- **Per-page Financials subnav** (an Electricity/Water/Telecom-style local `<nav class="subnav">` on every Bills/Transactions/Utilities page, pointing at sibling Financials screens): today's simple sidebar grouping satisfies "Financials becomes a tab" without inventing a new subnav-partial system with no second consumer yet.
- House/Health/Education/Vehicles/Legal & Identity tabs, and bank auto-capture: each gets its own plan later, per the brainstorm's build order (Q10, Q11).

---

## File Structure

```
app/models/domain.py                        NEW — Domain(str, Enum): FINANCIALS
app/models/document.py                      MODIFY — add domain field
app/models/todo.py                          MODIFY — add domain field
app/models/wiki.py                          MODIFY — add domain field to WikiPage
app/routers/bills.py                        MODIFY — prefix rename, domain= on create
app/routers/transactions.py                 MODIFY — prefix rename
app/routers/utilities.py                    MODIFY — prefix rename
app/services/todo_engine.py                 MODIFY — domain= on create
app/services/wiki_engine.py                 MODIFY — domain= on create
app/services/overview_service.py            MODIFY — url string prefixes
app/templates/base.html                     MODIFY — nav restructure
app/templates/bills/list.html               MODIFY — url prefixes
app/templates/bills/upload.html             MODIFY — url prefix
app/templates/transactions/list.html        MODIFY — url prefixes
app/templates/transactions/_rows.html       MODIFY — url prefixes
app/templates/transactions/needs_review.html MODIFY — url prefixes
app/templates/transactions/_needs_review_rows.html MODIFY — url prefixes
app/templates/utilities/tab.html            MODIFY — url prefixes
alembic/versions/718ee63973b8_*.py          NEW — domain columns + backfill
tests/test_document_model.py                MODIFY — domain field tests
tests/test_todo_model.py                    MODIFY — domain field tests
tests/test_wiki_model.py                    MODIFY — domain field tests
tests/test_bills_router.py                  MODIFY — url prefixes + domain test
tests/test_transactions_router.py           MODIFY — url prefixes
tests/test_utilities_router.py              MODIFY — url prefixes
tests/test_todo_engine.py                   MODIFY — domain assertion
tests/test_wiki_engine.py                   MODIFY — domain assertion
tests/test_overview_service.py              MODIFY — url prefixes
tests/test_dashboard_router.py              MODIFY — url prefixes
tests/test_e2e_bill_flow.py                 MODIFY — url prefixes
tests/test_main.py                          MODIFY — nav assertions
```

---

### Task 1: Domain tagging on Document, Todo, and WikiPage

**Files:**
- Create: `app/models/domain.py`
- Modify: `app/models/document.py`
- Modify: `app/models/todo.py`
- Modify: `app/models/wiki.py`
- Modify: `app/routers/bills.py`
- Modify: `app/services/todo_engine.py`
- Modify: `app/services/wiki_engine.py`
- Create: `alembic/versions/718ee63973b8_add_domain_to_documents_todos_and_wiki_pages.py`
- Test: `tests/test_document_model.py`
- Test: `tests/test_todo_model.py`
- Test: `tests/test_wiki_model.py`
- Test: `tests/test_bills_router.py`
- Test: `tests/test_todo_engine.py`
- Test: `tests/test_wiki_engine.py`

**Interfaces:**
- Produces: `app.models.domain.Domain` — `class Domain(str, Enum)` with member `FINANCIALS = "financials"`. `Document.domain: Optional[Domain]`, `Todo.domain: Optional[Domain]`, `WikiPage.domain: Optional[Domain]` — all default `None` at the Python level, all set to `Domain.FINANCIALS` at every current creation call site.

- [ ] **Step 1: Write failing tests for the `domain` field on all three models**

Append to `tests/test_document_model.py`:

```python
def test_document_domain_defaults_to_none(session):
    document = Document(
        filename="s.pdf", file_path="/tmp/s.pdf", content_hash="hash-domain-default",
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    assert document.domain is None


def test_document_domain_can_be_set(session):
    from app.models.domain import Domain

    document = Document(
        filename="s.pdf", file_path="/tmp/s.pdf", content_hash="hash-domain-set",
        source=DocumentSource.MANUAL, domain=Domain.FINANCIALS,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    assert document.domain == Domain.FINANCIALS
```

Append to `tests/test_todo_model.py`:

```python
def test_todo_domain_defaults_to_none(session):
    todo = Todo(title="Pay EDP", due_date=date(2026, 9, 5))
    session.add(todo)
    session.commit()
    session.refresh(todo)

    assert todo.domain is None


def test_todo_domain_can_be_set(session):
    from app.models.domain import Domain

    todo = Todo(title="Pay EDP", due_date=date(2026, 9, 5), domain=Domain.FINANCIALS)
    session.add(todo)
    session.commit()
    session.refresh(todo)

    assert todo.domain == Domain.FINANCIALS
```

Append to `tests/test_wiki_model.py`:

```python
def test_wiki_page_domain_defaults_to_none(session):
    page = WikiPage(topic="Water — provider & contract", facts_json="{}")
    session.add(page)
    session.commit()
    session.refresh(page)

    assert page.domain is None


def test_wiki_page_domain_can_be_set(session):
    from app.models.domain import Domain

    page = WikiPage(
        topic="Electricity — provider & contract", facts_json='{"provider": "EDP"}',
        domain=Domain.FINANCIALS,
    )
    session.add(page)
    session.commit()
    session.refresh(page)

    assert page.domain == Domain.FINANCIALS
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_document_model.py tests/test_todo_model.py tests/test_wiki_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.domain'` (or `TypeError: 'domain' is an invalid keyword argument`) for each new test.

- [ ] **Step 3: Create the `Domain` enum and add the field to all three models**

Create `app/models/domain.py`:

```python
"""Domain: which area of the household this record belongs to. FINANCIALS
is the only implemented domain today -- new members get added here as each
future domain tab (House, Health, Education, Vehicles, Legal & Identity)
ships. See brainstorms/2026-09-14-home-hub-tabs-restructure.md."""

from enum import Enum


class Domain(str, Enum):
    FINANCIALS = "financials"
```

Modify `app/models/document.py` — add the import and field:

```python
from app.models.domain import Domain
```

(add after the `sqlmodel` import) and add to the `Document` class, after the `doc_type` field:

```python
    doc_type: Optional[str] = None
    domain: Optional[Domain] = None
```

Modify `app/models/todo.py` — add the import and field:

```python
from app.models.domain import Domain
```

and add to the `Todo` class, after `transaction_id`:

```python
    transaction_id: Optional[int] = Field(default=None, foreign_key="transactions.id")
    domain: Optional[Domain] = None
```

Modify `app/models/wiki.py` — add the import and field to `WikiPage` only (not `WikiChange`):

```python
from app.models.domain import Domain
```

and add to the `WikiPage` class, after `facts_json`:

```python
    facts_json: str = Field(default="{}")
    domain: Optional[Domain] = None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_document_model.py tests/test_todo_model.py tests/test_wiki_model.py -v`
Expected: PASS (all 6 new tests, plus every pre-existing test in these 3 files still green).

- [ ] **Step 5: Write failing tests asserting `domain` gets set at every creation call site**

Append to `tests/test_bills_router.py`:

```python
def test_upload_bill_sets_financials_domain(client, monkeypatch, session):
    from app.models.domain import Domain

    async def fake_ingest_document(session, document):
        document.status = DocumentStatus.PROCESSED
        return document

    monkeypatch.setattr(bills_router, "ingest_document", fake_ingest_document)

    response = client.post(
        "/bills/upload",
        files={"file": ("bill.pdf", io.BytesIO(b"fake-pdf-bytes"), "application/pdf")},
        follow_redirects=False,
    )

    document_id = int(response.headers["location"].rsplit("/", 1)[-1])
    document = session.get(Document, document_id)
    assert document.domain == Domain.FINANCIALS
```

Append to `tests/test_todo_engine.py`:

```python
def test_generate_todo_for_transaction_sets_financials_domain(session):
    from app.models.domain import Domain

    transaction = _make_transaction(session, due_date=date(2026, 9, 5))

    todo = generate_todo_for_transaction(session, transaction)

    assert todo.domain == Domain.FINANCIALS
```

Append to `tests/test_wiki_engine.py`:

```python
@pytest.mark.asyncio
async def test_new_wiki_page_gets_financials_domain(session):
    from app.models.domain import Domain

    document, transaction = _make_document_and_transaction(session)
    response = json.dumps({
        "wiki_worthy": True,
        "topic": "Water — provider & contract",
        "facts": {"provider": "EPAL"},
    })
    client = _FakeAnthropicClient(response)

    await assess_and_update_wiki(session, document, transaction, client=client)

    page = session.exec(select(WikiPage).where(WikiPage.topic == "Water — provider & contract")).first()
    assert page.domain == Domain.FINANCIALS
```

- [ ] **Step 6: Run the new tests to verify they fail**

Run: `pytest tests/test_bills_router.py::test_upload_bill_sets_financials_domain tests/test_todo_engine.py::test_generate_todo_for_transaction_sets_financials_domain tests/test_wiki_engine.py::test_new_wiki_page_gets_financials_domain -v`
Expected: FAIL — `assert None == Domain.FINANCIALS` in all three (nothing sets `domain` yet).

- [ ] **Step 7: Set `domain=Domain.FINANCIALS` at each creation call site**

Modify `app/routers/bills.py` — add the import:

```python
from app.models.domain import Domain
```

and change the `Document(...)` construction in `upload_bill`:

```python
    document = Document(
        filename=file.filename, file_path=file_path, content_hash=content_hash,
        source=DocumentSource.MANUAL, status=DocumentStatus.PENDING,
        uploaded_by=getattr(request.state, "user_email", None),
        account_id=int(account_id) if account_id else None,
        domain=Domain.FINANCIALS,
    )
```

Modify `app/services/todo_engine.py` — add the import:

```python
from app.models.domain import Domain
```

and change the `Todo(...)` construction:

```python
    todo = Todo(
        title=f"Pay {transaction.provider} — {transaction.amount:.2f} {transaction.currency}",
        due_date=transaction.due_date,
        transaction_id=transaction.id,
        domain=Domain.FINANCIALS,
    )
```

Modify `app/services/wiki_engine.py` — add the import:

```python
from app.models.domain import Domain
```

and change the `WikiPage(...)` construction inside the `if page is None:` branch:

```python
        page = WikiPage(topic=topic, facts_json=json.dumps(new_facts), domain=Domain.FINANCIALS)
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/test_bills_router.py tests/test_todo_engine.py tests/test_wiki_engine.py -v`
Expected: PASS (all tests in these 3 files, including the 3 new ones).

- [ ] **Step 9: Create the migration**

Create `alembic/versions/718ee63973b8_add_domain_to_documents_todos_and_wiki_pages.py`:

```python
"""add domain to documents, todos, and wiki_pages

Revision ID: 718ee63973b8
Revises: 650bb8ad7210
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '718ee63973b8'
down_revision: Union[str, Sequence[str], None] = '650bb8ad7210'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("domain", sa.Enum("FINANCIALS", name="domain"), nullable=True))
    with op.batch_alter_table("todos", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("domain", sa.Enum("FINANCIALS", name="domain"), nullable=True))
    with op.batch_alter_table("wiki_pages", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("domain", sa.Enum("FINANCIALS", name="domain"), nullable=True))

    op.execute("UPDATE documents SET domain = 'FINANCIALS' WHERE domain IS NULL")
    op.execute("UPDATE todos SET domain = 'FINANCIALS' WHERE domain IS NULL")
    op.execute("UPDATE wiki_pages SET domain = 'FINANCIALS' WHERE domain IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("documents", recreate="always") as batch_op:
        batch_op.drop_column("domain")
    with op.batch_alter_table("todos", recreate="always") as batch_op:
        batch_op.drop_column("domain")
    with op.batch_alter_table("wiki_pages", recreate="always") as batch_op:
        batch_op.drop_column("domain")
```

- [ ] **Step 10: Verify the migration against a scratch copy of the real database**

Run:

```bash
cp "data/home_family.db" /tmp/scratch_home_family.db
DATABASE_PATH=/tmp/scratch_home_family.db alembic upgrade head
sqlite3 /tmp/scratch_home_family.db "SELECT domain, COUNT(*) FROM documents GROUP BY domain;"
sqlite3 /tmp/scratch_home_family.db "SELECT domain, COUNT(*) FROM todos GROUP BY domain;"
sqlite3 /tmp/scratch_home_family.db "SELECT domain, COUNT(*) FROM wiki_pages GROUP BY domain;"
DATABASE_PATH=/tmp/scratch_home_family.db alembic downgrade -1
DATABASE_PATH=/tmp/scratch_home_family.db alembic upgrade head
rm /tmp/scratch_home_family.db
```

Expected: each `SELECT ... GROUP BY domain` returns exactly one row, `FINANCIALS|<total row count for that table>` — no `NULL` group. The downgrade/upgrade round-trip completes without error.

- [ ] **Step 11: Run the full test suite**

Run: `pytest -v`
Expected: PASS, no regressions.

- [ ] **Step 12: Commit**

```bash
git add app/models/domain.py app/models/document.py app/models/todo.py app/models/wiki.py \
        app/routers/bills.py app/services/todo_engine.py app/services/wiki_engine.py \
        alembic/versions/718ee63973b8_add_domain_to_documents_todos_and_wiki_pages.py \
        tests/test_document_model.py tests/test_todo_model.py tests/test_wiki_model.py \
        tests/test_bills_router.py tests/test_todo_engine.py tests/test_wiki_engine.py
git commit -m "feat: tag Document, Todo, and WikiPage with a domain

Generalizes the shared ingestion layer ahead of the House/Health/
Education/Vehicles/Legal & Identity tabs -- see
brainstorms/2026-09-14-home-hub-tabs-restructure.md Q2. Only
FINANCIALS exists today; every current creation call site sets it
explicitly, historical rows are backfilled in the same migration."
```

---

### Task 2: Rename Bills routes to `/financials/bills`

**Files:**
- Modify: `app/routers/bills.py:18,45,58`
- Modify: `app/templates/bills/list.html:5,11`
- Modify: `app/templates/bills/upload.html:5`
- Modify: `app/services/overview_service.py:439`
- Test: `tests/test_bills_router.py`
- Test: `tests/test_e2e_bill_flow.py`
- Test: `tests/test_overview_service.py:440`

**Interfaces:**
- Consumes: `Domain.FINANCIALS` set on `Document` creation (Task 1) — unaffected by this rename.
- Produces: every Bills route now lives under `/financials/bills*` instead of `/bills*`.

- [ ] **Step 1: Update every test assertion to the new `/financials/bills` prefix**

In `tests/test_bills_router.py`, replace every occurrence of the literal string `/bills` with `/financials/bills` (`replace_all: true` — the file has no other use of that substring, confirmed by grep).

In `tests/test_e2e_bill_flow.py`, replace every occurrence of the literal string `/bills/upload` with `/financials/bills/upload` (`replace_all: true`; 3 occurrences).

In `tests/test_overview_service.py`, change line 440:

```python
    assert doc_item.url.startswith("/bills/")
```

to:

```python
    assert doc_item.url.startswith("/financials/bills/")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_bills_router.py tests/test_e2e_bill_flow.py tests/test_overview_service.py -v`
Expected: FAIL — routes still respond at the old `/bills` prefix, so requests to `/financials/bills*` 404, and `doc_item.url` still starts with `/bills/`.

- [ ] **Step 3: Rename the router prefix, redirects, templates, and the overview service reference**

In `app/routers/bills.py` line 18:

```python
router = APIRouter(prefix="/financials/bills", tags=["bills"])
```

Line 45:

```python
        return RedirectResponse(f"/financials/bills/{existing.id}", status_code=303)
```

Line 58:

```python
    return RedirectResponse(f"/financials/bills/{document.id}", status_code=303)
```

In `app/templates/bills/list.html`, replace every occurrence of `/bills` with `/financials/bills` (`replace_all: true`; 2 occurrences, lines 5 and 11).

In `app/templates/bills/upload.html` line 5:

```html
<form action="/financials/bills/upload" method="post" enctype="multipart/form-data">
```

In `app/services/overview_service.py` line 439:

```python
            url=f"/financials/bills/{doc.id}",
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_bills_router.py tests/test_e2e_bill_flow.py tests/test_overview_service.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: PASS, no regressions (in particular `tests/test_dashboard_router.py`'s Bills-related assertions, if any, and `tests/test_main.py`, which is updated in Task 5, not here).

- [ ] **Step 6: Commit**

```bash
git add app/routers/bills.py app/templates/bills/list.html app/templates/bills/upload.html \
        app/services/overview_service.py tests/test_bills_router.py tests/test_e2e_bill_flow.py \
        tests/test_overview_service.py
git commit -m "refactor: move Bills routes under /financials/bills"
```

---

### Task 3: Rename Transactions routes to `/financials/transactions`

**Files:**
- Modify: `app/routers/transactions.py:19`
- Modify: `app/templates/transactions/list.html`
- Modify: `app/templates/transactions/_rows.html`
- Modify: `app/templates/transactions/needs_review.html`
- Modify: `app/templates/transactions/_needs_review_rows.html`
- Modify: `app/services/overview_service.py` (9 occurrences: lines 104, 109, 114, 170, 198, 245, 246, 322, 414, 429 — all literal `/transactions` prefixes)
- Test: `tests/test_transactions_router.py`
- Test: `tests/test_dashboard_router.py:69-70`
- Test: `tests/test_overview_service.py` (10 occurrences: lines 67-69, 99, 175, 221-222, 258, 426, 438)

**Interfaces:**
- Consumes: nothing from Task 1 or 2.
- Produces: every Transactions route now lives under `/financials/transactions*` instead of `/transactions*`.

- [ ] **Step 1: Update every test assertion to the new `/financials/transactions` prefix**

In `tests/test_transactions_router.py`, replace every occurrence of the literal string `/transactions` with `/financials/transactions` (`replace_all: true`; 20 occurrences, confirmed by grep — all are `client.get("/transactions"...)` / `client.post("/transactions/..."...)` calls).

In `tests/test_dashboard_router.py`, replace every occurrence of the literal string `/transactions` with `/financials/transactions` (`replace_all: true`; exactly 2 occurrences in the whole file, both in `test_overview_page_kpi_drill_down_links_present`, lines 69-70):

```python
    assert 'href="/financials/transactions?transaction_type=credit' in response.text
    assert 'href="/financials/transactions?transaction_type=debit' in response.text
```

In `tests/test_overview_service.py`, replace every occurrence of the literal string `/transactions` with `/financials/transactions` (`replace_all: true`; 10 occurrences at lines 67-69, 99, 175, 221-222, 258, 426, 438).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_transactions_router.py tests/test_dashboard_router.py tests/test_overview_service.py -v`
Expected: FAIL — routes still respond at the old `/transactions` prefix, and `overview_service.py` still builds old-prefixed URLs.

- [ ] **Step 3: Rename the router prefix, templates, and the overview service references**

In `app/routers/transactions.py` line 19:

```python
router = APIRouter(prefix="/financials/transactions", tags=["transactions"])
```

In `app/templates/transactions/list.html`, replace every occurrence of `/transactions` with `/financials/transactions` (`replace_all: true`; 7 occurrences at lines 6, 7, 10, 12, 37, 75, 79).

In `app/templates/transactions/_rows.html`, replace every occurrence of `/transactions` with `/financials/transactions` (`replace_all: true`; 2 occurrences at lines 8, 18).

In `app/templates/transactions/needs_review.html`, replace every occurrence of `/transactions` with `/financials/transactions` (`replace_all: true`; 2 occurrences at lines 6, 7).

In `app/templates/transactions/_needs_review_rows.html`, replace every occurrence of `/transactions` with `/financials/transactions` (`replace_all: true`; 6 occurrences at lines 11, 29, 48, 58, 69, 78).

In `app/services/overview_service.py`, replace every occurrence of the literal string `/transactions` with `/financials/transactions` (`replace_all: true`; 9 occurrences at lines 104, 109, 114, 170, 198, 245, 246, 322, 414, 429 — the file's `/bills` occurrence at line 439 was already renamed in Task 2 and is untouched by this substring).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_transactions_router.py tests/test_dashboard_router.py tests/test_overview_service.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/routers/transactions.py app/templates/transactions/ app/services/overview_service.py \
        tests/test_transactions_router.py tests/test_dashboard_router.py tests/test_overview_service.py
git commit -m "refactor: move Transactions routes under /financials/transactions"
```

---

### Task 4: Rename Utilities routes to `/financials/utilities`

**Files:**
- Modify: `app/routers/utilities.py:11,40`
- Modify: `app/templates/utilities/tab.html:7-9`
- Test: `tests/test_utilities_router.py`
- Test: `tests/test_e2e_bill_flow.py:205` (already touched in Task 2 for `/bills/upload`; this step adds the `/utilities/electricity` reference in the same file)

**Interfaces:**
- Consumes: nothing from Tasks 1-3.
- Produces: every Utilities route now lives under `/financials/utilities*` instead of `/utilities*`.

- [ ] **Step 1: Update every test assertion to the new `/financials/utilities` prefix**

In `tests/test_utilities_router.py`, replace every occurrence of the literal string `/utilities` with `/financials/utilities` (`replace_all: true`; 6 occurrences: lines 17, 19, 23, 28, 43, 59).

In `tests/test_e2e_bill_flow.py` line 205:

```python
    electricity_response = client.get("/financials/utilities/electricity")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_utilities_router.py tests/test_e2e_bill_flow.py -v`
Expected: FAIL — routes still respond at the old `/utilities` prefix.

- [ ] **Step 3: Rename the router prefix, redirect, and template**

In `app/routers/utilities.py` line 11:

```python
router = APIRouter(prefix="/financials/utilities", tags=["utilities"])
```

Line 40:

```python
async def utilities_root():
    return RedirectResponse("/financials/utilities/electricity")
```

In `app/templates/utilities/tab.html`, replace every occurrence of `/utilities` with `/financials/utilities` (`replace_all: true`; 3 occurrences at lines 7, 8, 9).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_utilities_router.py tests/test_e2e_bill_flow.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/routers/utilities.py app/templates/utilities/tab.html \
        tests/test_utilities_router.py tests/test_e2e_bill_flow.py
git commit -m "refactor: move Utilities routes under /financials/utilities"
```

---

### Task 5: Restructure the sidebar nav to group Financials

**Files:**
- Modify: `app/templates/base.html`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: the `/financials/bills`, `/financials/transactions`, `/financials/utilities/electricity` paths from Tasks 2-4.
- Produces: nothing consumed by a later task in this plan — this is the last task.

- [ ] **Step 1: Update the nav assertions in `test_main.py`**

Modify `tests/test_main.py`:

```python
def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Overview" in response.text
    assert "Financials" in response.text
    assert "Bills & Bank" in response.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_main.py -v`
Expected: FAIL — `assert "Overview" in response.text` fails; the nav link still reads "Dashboard" and there is no "Financials" grouping label.

- [ ] **Step 3: Restructure the nav**

Modify `app/templates/base.html`. Replace the `<nav>` block (lines 123-130):

```html
  <nav>
    <a href="/">Dashboard</a>
    <a href="/bills">Bills & Bank</a>
    <a href="/transactions">Transactions</a>
    <a href="/utilities/electricity">Utilities</a>
    <a href="/wiki">Wiki</a>
    <a href="/todos">To-Dos</a>
  </nav>
```

with:

```html
  <nav>
    <a href="/">Overview</a>
    <div class="nav-group">
      <span class="nav-group-label">Financials</span>
      <a href="/financials/bills">Bills & Bank</a>
      <a href="/financials/transactions">Transactions</a>
      <a href="/financials/utilities/electricity">Utilities</a>
    </div>
    <a href="/wiki">Wiki</a>
    <a href="/todos">To-Dos</a>
  </nav>
```

Add two rules to the `<style>` block (after the existing `nav a:hover { text-decoration: underline; }` rule, line 11):

```css
    .nav-group { margin-bottom: 0.5rem; }
    .nav-group-label { display: block; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: #999; margin: 0.75rem 0 0.35rem; }
    .nav-group a { padding-left: 0.5rem; }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_main.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/templates/base.html tests/test_main.py
git commit -m "feat: group Bills/Transactions/Utilities under a Financials nav section"
```

---

## Deploy note

This branch's changes are code + a schema migration only — no new environment variables, no new systemd units. Deploying is the existing `git subtree push --prefix="Home & Family" home_hub main` flow (see `docs/SYSADMIN.md` §3); `alembic upgrade head` runs automatically as part of that deploy and will pick up migration `718ee63973b8`.
