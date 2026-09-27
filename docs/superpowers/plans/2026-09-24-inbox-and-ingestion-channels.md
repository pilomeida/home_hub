# Inbox + Email & Telegram Ingestion Channels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Documents that arrive by email (to a dedicated Hub mailbox) or by Telegram (to a dedicated Hub bot) land in a shared, top-level **Inbox**, with a domain and category suggested by an LLM. A document is filed into its domain only after a human approves it, and only then is it ingested into the wiki.

**Architecture:**
- **Channels.** Two thin channel adapters, each a separate OS process: an IMAP poller run by a systemd timer, and a Telegram polling bot run as a long-lived systemd service. Each turns incoming files into Plan A's `IncomingFile` and calls one function, `inbox_service.receive_document()`.
- **Intake.** `receive_document()` calls Plan A's `receive_file(..., initial_status=PENDING_REVIEW)`, which creates an unclassified document with `domain = NULL`. It then asks a registry-driven classifier for a suggestion and records it in a new `inbox_items` row.
- **Review.** The `/inbox` page lists pending items. Each card includes Plan A's generic domain-field inputs.
- **Approve.** Calls Plan A's `validate_classification()`, showing its `.errors` in the form. It then writes a `WikiOperation.REVIEW` log entry and calls Plan A's `finalize_document()`. That is the same core a manual upload uses: the domain handler runs, and the wiki Ingest happens there and nowhere else.
- **Discard.** Marks the document `DISCARDED` and logs a REVIEW entry. The row and the file are kept.
- **Scope.** The Inbox handles *first-time filing* only. Correcting domain, category or fields after filing is Plan A's generic re-filing (`/documents/{id}/edit`, `/records/{id}/edit`); this plan builds no post-approval editor.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy, Alembic (SQLite, `render_as_batch=True`), Jinja2 + htmx, Anthropic SDK (Claude Haiku 4.5), stdlib `imaplib`/`email`, `python-telegram-bot` v21+, systemd **user** units, pytest + pytest-asyncio.

**Spec:**
- `/home/pedro/Desktop/Claude_Corner/brainstorms/2026-09-24-house-tab-design.md`: Q5–Q9, Q22–Q24, and "Standing patterns → Document ingestion channels".
- `/home/pedro/Desktop/Claude_Corner/brainstorms/2026-09-14-home-hub-tabs-restructure.md`: Q18 (git-deployed timer).
- Contracts: `docs/superpowers/plans/2026-09-24-house-tab.md` (Plan A). The relevant parts are Tasks 2–4, 6–8, 15 and 19–24, plus "Contracts summary for Plans B and C".

**Ships after:** Plan A through its Task 25: `WikiOperation.REVIEW` (Task 19), `Record`/RECORD categories and generic re-filing (Tasks 20–24), final migration head `e7b3d1f4a6c8`. Plan A must be merged before starting Task 1.

---

## Plan A contracts this plan uses (source of truth: Plan A)

```python
# app/domains/registry.py
implemented_domains() -> list[DomainSpec]
get_spec(domain) -> DomainSpec            # raises UnknownDomainError
is_implemented(domain) -> bool
document_url(document) -> Optional[str]   # Plan B EXTENDS this (Task 5): pending-review docs -> their Inbox card

# app/domains/base.py
DomainSpec(domain, label, description, home_url, categories, fields, handler, nav_links,
           overview_card, document_url, wiki, infers_category)
    .category(value) / .category_label(value) / .fields_for(category, media_kind)
CategorySpec(value, label, description, accepted_media, kind: SourceKind = DOCUMENT)
SourceKind (DOCUMENT, RECORD)      # RECORD = entry-style category, e.g. House maintenance_log
registry.record_url(record) -> Optional[str]
# app/domains/entries.py
record_for_document(session, document) -> Optional[Record]   # the live Record a file is attached to
MediaKind  (PDF, IMAGE, VIDEO, OTHER)

# app/domains/fields.py
InvalidClassification(ValueError).errors: dict[str, str]   # keys "domain" | "category" | "file" | <field key>
media_kind_for(filename) -> MediaKind
build_form_fields(session, spec, category, values, errors, media_kind=None) -> list[FormField]
file_url(document) -> str
# template: app/templates/domains/_field_inputs.html  (renders `form_fields`)

# app/services/ingestion.py
IncomingFile(filename, content, source, uploaded_by=None)
Classification(domain: Domain, category: Optional[str] = None, fields: Mapping = {})
ReceiveResult(document, duplicate)
receive_file(session, incoming, initial_status=DocumentStatus.PENDING) -> ReceiveResult   # domain=None
validate_classification(session, classification, filename) -> ValidatedClassification     # raises InvalidClassification
async finalize_document(session, document, classification) -> Document
    # DOCUMENT category: sets domain/category/fields, runs handler (+ wiki Ingest).
    # RECORD category:   creates Record(fields, document_id=document.id, entered_by=uploaded_by);
    #                    document gets domain/category, fields_json="{}", PROCESSED; handler.process_record runs.
# Re-filing after finalization (refile_document / refile_record, /documents/{id}/edit, /records/{id}/edit): Plan A only.

# app/services/wiki_store.py + app/models/wiki.py
append_log(session, operation: WikiOperation, description, *, document_id=None, page_ids=(), commit=True)
WikiOperation.REVIEW    # Plan A Task 19

# app/templating.py
templates   # the one Jinja2Templates; globals domain_nav(), domain_label()
```

**Invariant (Plan A):** `Document.domain IS NOT NULL` ⇔ the document has been finalized.
- Plan B never sets `domain` itself. Only `finalize_document` does, and Plan B calls it only from `approve()`.
- So pending and discarded documents never reach a domain handler, the wiki, the index, or any domain tab.
- Approval into a RECORD category is the same single call: `finalize_document` creates the Record and attaches the file. Plan B has no record-specific path; the category picker and the field form work for both kinds because both come from the registry and `build_form_fields`.
- After finalization, corrections go through Plan A's re-filing screens. The Inbox never re-opens a filed document.

## What this plan provides to other plans

- **Statuses and source:** `DocumentStatus.PENDING_REVIEW` and `DocumentStatus.DISCARDED`; `DocumentSource.TELEGRAM`.
- **`inbox_items` table** (`app.models.inbox_item.InboxItem`): channel provenance, the classifier's suggestion, and a review audit. `suggested_domain` is a plain **string** validated against the registry, so new domains need no migration.
- **Classifier:** `app.services.domain_classifier.suggest_domain_and_category(file_path, context_text=None, *, domains=None, client=None) -> DomainSuggestion`. The prompt is built from `spec.description` and the category descriptions; no domain is named in code.
- **Channel entry point:** `app.services.inbox_service.receive_document(session, incoming: IncomingFile, *, context_text, external_ref) -> InboxReceipt`. This is the single entry point for any future channel (WhatsApp, a generic "drop into Inbox" upload, and so on).
- **`registry.document_url()` extended:** a `PENDING_REVIEW` document links to `/inbox#inbox-<id>`.
- **Wiki log:** `WikiOperation.REVIEW` entries whose description starts with `Approved …` or `Discarded …`.
- **Deploy mechanism:** git-deployed **systemd user units** (`deploy/systemd/*`, `deploy/install_user_units.sh`). The bank-sync plan should drop its `.service`/`.timer` files into the same folder.

## Global Constraints

- **One ingestion path.** Channels call `inbox_service.receive_document()`, which calls Plan A's `receive_file()`. Approval calls Plan A's `validate_classification()` and then `finalize_document()`. Plan B creates no `Document` rows itself, has no store or dispatch functions of its own, and no Inbox code runs extraction, todo or wiki logic.
- **No domain or category names in Plan B code.** None appears as a literal in the classifier, inbox service, router, templates or channels; everything is read from the registry. Tests use fake `DomainSpec`s built with Plan A's `tests/domain_fakes.make_fake_spec`.
- **Nothing is auto-finalized from classification alone.** Every channel document starts `PENDING_REVIEW` with `domain = NULL`.
- **Raw sources are immutable.** Discard never deletes the file or the row.
- **One template environment.** Every template renders through `app.templating.templates`.
- **Migrations are additive-only:** one new table plus widened enums. Test them against a **fresh scratch copy of the production DB**, because the local `data/home_family.db` is out of date.
- **Secrets live only in `/srv/home-hub/app/.env`** on the VPS. `.env.example` gets placeholder names only. Never commit a token, a password or a real Telegram ID.
- **The web app must boot with every new setting unset.** That covers tests, local dev, and the first deploy before Pedro's manual setup. Channel processes exit 0 with a clear message when their settings are unset.
- **LLM classification uses Claude Haiku 4.5** (`claude-haiku-4-5-20251001`), the same model as the existing document classifier.
- **Never `git push`.** Commit only when the executing session has Pedro's order to commit (per `Personal/CLAUDE.md`).

## Decisions made in this plan (one line each)

- **The suggestion lives in `inbox_items`, never on `Document.domain`.** This keeps Plan A's "domain set ⇔ finalized" invariant intact.
- **Status lifecycle.** Channel intake starts at `PENDING_REVIEW`. Approve hands the document to `finalize_document`, which moves it to `PENDING` and then `PROCESSED | NEEDS_ATTENTION`. Discard moves it to `DISCARDED`. Review state is derived from `Document.status`; there is no second status field.
- **`suggested_domain` is a string, not a DB enum,** so a future domain becomes suggestible just by registering.
- **The Telegram bot runs as its own systemd service, not inside the web process.** This follows the 2026-09-14 rule that background work runs as separate, crash-isolated processes. The library and polling pattern are reused from Recipes.
- **systemd *user* units (`loginctl enable-linger home-hub`) instead of a broader sudo rule.** Unit files written under sudo can run as root (for example via `ExecStartPre=+…`), which would make the deploy key root-equivalent. The coordinator accepted this.
- **Mail is handled by moving it between folders, not by read/unread flags.** A person browsing the mailbox can't disturb the poller, and content-hash dedup makes reruns harmless.
- **Email gets a sender allowlist too.** The address will eventually leak to spam, and every attachment costs an LLM call.
- **No WAL mode.** Writes are tiny and infrequent, and the default 5-second busy timeout is enough. WAL would silently break the documented `cp home_family.db` backup habit.

---

## Manual setup steps: Pedro (plain language, do these once)

The agent can't do these for you. They aren't needed for the *building*: the app deploys and runs fine without them, with the email and Telegram parts switched off. They're needed to *switch those parts on*.

**M1. Create the Hub's own email address.**
1. Create a new Gmail account only for the Hub, for example `cdafamily.hub@gmail.com` (any free name works). Don't use your personal Gmail.
2. In that account, go to Google Account → Security and turn on **2-Step Verification**.
3. Then go to Google Account → Security → **App passwords** and create one called "Home Hub". Google shows a 16-letter password once. Keep it for step M4.
4. Write down which email addresses may send documents to the Hub (yours, Rute's, the boys'). Mail from any other address will be ignored.

**M2. Create the Hub's Telegram bot.**
1. In Telegram, search for **@BotFather** (it has a blue tick) and open it.
2. Send `/newbot`. For the name, type `Home Hub`. For the username, type something ending in `bot`, for example `cda_home_hub_bot`.
3. BotFather replies with a long **token** that looks like `123456:ABC-...`. Keep it for step M4. Anyone with this token controls the bot, so don't share it anywhere else.

**M3. Everyone says hello to the bot.** Do this only after the agent tells you the bot is running.
1. Pedro, Rute, Matias and Vicente each open `t.me/<the username from M2>`, tap **Start**, and send any message (for example "hi").
2. The bot won't answer yet, and that's expected. The agent reads your four Telegram IDs from the server log and switches you on.

**M4. Hand over the secrets.** Give the implementing agent, in your session:
- the Hub email address;
- the 16-letter app password;
- the allowed sender addresses;
- the bot token.

The agent puts them only in the server's private settings file, the same place the Anthropic key lives. They never go into the code or GitHub. If you'd rather type them in yourself, ask the agent to walk you through it.

## Operator steps (implementing agent, via root SSH, with Pedro's go-ahead each time)

- **O1. Before the first push of this plan:** run `ssh root@167.233.51.113 'loginctl enable-linger home-hub && ls -d /run/user/995'`. This lets `home-hub` run its own background services; the deploy fails without it.
- **O2. After M1, M2 and M4:**
  1. Back up `/srv/home-hub/app/.env`.
  2. Append the `HUB_*` settings listed in Task 6, **and the LLM-gateway settings**:
     `LLMSEL_URL=http://127.0.0.1:8010`, `LLMSEL_WORKER=hub`, and `LLMSEL_TOKEN=<token issued by the
     gateway admin>`. The Hub makes no direct provider calls and holds no provider key.
  3. Run `chown home-hub:home-hub` and `chmod 600` on the file.
  4. Restart the bot: `sudo -u home-hub XDG_RUNTIME_DIR=/run/user/995 systemctl --user restart home-hub-telegram`.
- **O3. After M3:**
  1. Read the IDs: `journalctl _SYSTEMD_USER_UNIT=home-hub-telegram.service --since today | grep "unknown Telegram user"`.
  2. Add `HUB_TELEGRAM_ALLOWED_USERS=<id>:Pedro,<id>:Rute,<id>:Matias,<id>:Vicente` to `.env`.
  3. Restart as in O2.
- **O4.** Smoke test (Task 10).

---

## File Structure

```
app/models/document.py                  MODIFY — DocumentStatus +PENDING_REVIEW,+DISCARDED; DocumentSource +TELEGRAM
app/models/inbox_item.py                NEW    — InboxItem (provenance, suggestion, review audit)
app/models/__init__.py                  MODIFY — register InboxItem
alembic/versions/c4b8e2d91f07_add_inbox_items_and_review_statuses.py  NEW (down_revision e7b3d1f4a6c8)
app/services/document_input.py          MODIFY — is_model_readable(), +webp
app/services/domain_classifier.py       NEW    — registry-driven domain+category suggestion (Haiku)
app/services/inbox_service.py           NEW    — receive_document / pending_entries / approve / discard
app/domains/registry.py                 MODIFY — document_url(): pending-review docs -> Inbox card
app/routers/inbox.py                    NEW    — /inbox page, fields partial, approve, discard
app/templates/inbox/list.html           NEW
app/templates/inbox/_entry.html         NEW    — one pending card (form)
app/templates/inbox/_fields.html        NEW    — area/type selects + domains/_field_inputs.html
app/templates/inbox/_result.html        NEW    — card replacement after approve/discard
app/templates/base.html                 MODIFY — "Inbox" top-level nav link + card styles
app/main.py                             MODIFY — include inbox router
app/config.py, .env.example             MODIFY — HUB_* channel settings (lazy-parsed)
app/channels/__init__.py                NEW
app/channels/email_parsing.py           NEW    — raw RFC822 bytes -> ParsedEmail (pure)
app/channels/email_poller.py            NEW    — Mailbox protocol, ImapMailbox, poll_once, main()
app/channels/telegram_bot.py            NEW    — pick_file, file_to_inbox, PTB wiring, main()
requirements.txt                        MODIFY — python-telegram-bot>=21.3
deploy/systemd/home-hub-mailpoll.{service,timer}, home-hub-telegram.service  NEW
deploy/install_user_units.sh            NEW
.github/workflows/deploy.yml            MODIFY — install/refresh user units, check none failed
docs/ARCHITECTURE.md, docs/SYSADMIN.md  MODIFY
tests/conftest.py                       MODIFY — two_domains + inbox_documents_dir fixtures
tests/test_inbox_item_model.py, test_domain_classifier.py, test_inbox_service.py,
tests/test_inbox_router.py, test_config_channels.py, test_email_parsing.py,
tests/test_email_poller.py, test_telegram_bot.py, test_deploy_units.py        NEW
tests/test_document_input.py, tests/test_domain_registry.py                   MODIFY
```

---

### Task 1: Review status model + `InboxItem` + migration

**Files:**
- Modify: `app/models/document.py`, `app/models/__init__.py`
- Create: `app/models/inbox_item.py`, `alembic/versions/c4b8e2d91f07_add_inbox_items_and_review_statuses.py`
- Test: `tests/test_inbox_item_model.py`

**Interfaces:**
- Produces:
  - `DocumentStatus.PENDING_REVIEW = "pending_review"`
  - `DocumentStatus.DISCARDED = "discarded"`
  - `DocumentSource.TELEGRAM = "telegram"`
  - `InboxItem(id, document_id (unique), context_text, external_ref, suggested_domain: Optional[str], suggested_category: Optional[str], confidence: Optional[float], classifier_note: Optional[str], received_at, reviewed_by, reviewed_at)`

- [ ] **Step 1: Write the failing tests.** Create `tests/test_inbox_item_model.py`:

```python
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.inbox_item import InboxItem


def _document(session, content_hash="h-inbox"):
    document = Document(
        filename="scan.jpg", file_path="/tmp/scan.jpg", content_hash=content_hash,
        source=DocumentSource.TELEGRAM, status=DocumentStatus.PENDING_REVIEW,
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def test_new_statuses_and_source_exist():
    assert DocumentStatus.PENDING_REVIEW.value == "pending_review"
    assert DocumentStatus.DISCARDED.value == "discarded"
    assert DocumentSource.TELEGRAM.value == "telegram"


def test_inbox_item_round_trip(session):
    document = _document(session)
    item = InboxItem(
        document_id=document.id, context_text="Telegram caption: boiler warranty",
        external_ref="telegram:1:2", suggested_domain="some_future_domain",
        suggested_category="anything", confidence=0.91, classifier_note="looks like it",
    )
    session.add(item)
    session.commit()
    session.refresh(item)

    assert item.suggested_domain == "some_future_domain"   # plain string, no DB enum
    assert isinstance(item.received_at, datetime)
    assert item.reviewed_by is None and item.reviewed_at is None


def test_inbox_item_one_per_document(session):
    document = _document(session, content_hash="h-unique")
    session.add(InboxItem(document_id=document.id))
    session.commit()
    session.add(InboxItem(document_id=document.id))
    with pytest.raises(IntegrityError):
        session.commit()
```

- [ ] **Step 2: Run** `pytest tests/test_inbox_item_model.py -v`. Expected: FAIL (`No module named 'app.models.inbox_item'`).

- [ ] **Step 3: Implement.** In `app/models/document.py`, append the new members to the two enums. Keep the existing members and their order:

```python
class DocumentSource(str, Enum):
    MANUAL = "manual"
    EMAIL = "email"
    API = "api"
    TELEGRAM = "telegram"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    NEEDS_ATTENTION = "needs_attention"
    # Channel intake (email/Telegram): waiting in the Inbox for a human to
    # approve a domain + category. Always unfinalized (domain = NULL).
    PENDING_REVIEW = "pending_review"
    # Rejected in the Inbox. Row and file are kept (raw sources are immutable).
    DISCARDED = "discarded"
```

Create `app/models/inbox_item.py`:

```python
"""InboxItem: channel provenance + the classifier's suggestion + review audit
for a Document that arrived through an ingestion channel (email, Telegram).

Review state lives in Document.status (PENDING_REVIEW -> finalized, or
DISCARDED); this row never duplicates it. The suggestion is kept here, NOT
on Document.domain, because "domain IS NOT NULL" means "finalized".
suggested_domain is a Domain *value* string validated against the registry
when written, so registering a new domain needs no migration here."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class InboxItem(SQLModel, table=True):
    __tablename__ = "inbox_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", unique=True, index=True)
    context_text: Optional[str] = None      # email subject + snippet, or Telegram caption
    external_ref: Optional[str] = None      # "email:<Message-ID>#<n>" / "telegram:<chat>:<msg>"
    suggested_domain: Optional[str] = None  # Domain value, e.g. "house"
    suggested_category: Optional[str] = None
    confidence: Optional[float] = None      # 0.0-1.0 as reported by the classifier
    classifier_note: Optional[str] = None   # classifier's one-line reason, or why it couldn't run
    received_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
```

In `app/models/__init__.py`, add `from app.models.inbox_item import InboxItem  # noqa: F401` after the `Document` import.

- [ ] **Step 4: Run** `pytest tests/test_inbox_item_model.py -v`. Expected: PASS (3 tests).

- [ ] **Step 5: Write the migration.** Run `alembic heads`. It must print `e7b3d1f4a6c8`, which is Plan A's final head. If it prints anything else, stop and ask the coordinator for the current head. Then create `alembic/versions/c4b8e2d91f07_add_inbox_items_and_review_statuses.py`:

```python
"""add inbox_items table and review statuses

Revision ID: c4b8e2d91f07
Revises: e7b3d1f4a6c8
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "c4b8e2d91f07"
down_revision: Union[str, Sequence[str], None] = "e7b3d1f4a6c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_STATUS = ("PENDING", "PROCESSED", "NEEDS_ATTENTION")
_NEW_STATUS = _OLD_STATUS + ("PENDING_REVIEW", "DISCARDED")
_OLD_SOURCE = ("MANUAL", "EMAIL", "API")
_NEW_SOURCE = _OLD_SOURCE + ("TELEGRAM",)


def upgrade() -> None:
    with op.batch_alter_table("documents", recreate="always") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum(*_OLD_STATUS, name="documentstatus"),
            type_=sa.Enum(*_NEW_STATUS, name="documentstatus"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "source",
            existing_type=sa.Enum(*_OLD_SOURCE, name="documentsource"),
            type_=sa.Enum(*_NEW_SOURCE, name="documentsource"),
            existing_nullable=False,
        )

    op.create_table(
        "inbox_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("context_text", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("external_ref", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("suggested_domain", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("suggested_category", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("classifier_note", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_inbox_items_document_id"), "inbox_items", ["document_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_inbox_items_document_id"), table_name="inbox_items")
    op.drop_table("inbox_items")
    with op.batch_alter_table("documents", recreate="always") as batch_op:
        batch_op.alter_column(
            "source",
            existing_type=sa.Enum(*_NEW_SOURCE, name="documentsource"),
            type_=sa.Enum(*_OLD_SOURCE, name="documentsource"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum(*_NEW_STATUS, name="documentstatus"),
            type_=sa.Enum(*_OLD_STATUS, name="documentstatus"),
            existing_nullable=False,
        )
```

- [ ] **Step 6: Verify the migration on a fresh scratch copy of production.** The SSH call only reads, but ask Pedro before making it.

```bash
mkdir -p /tmp/claude-1000/migcheck
scp root@167.233.51.113:/srv/home-hub/app/data/home_family.db /tmp/claude-1000/migcheck/prod_copy.db
DATABASE_PATH=/tmp/claude-1000/migcheck/prod_copy.db alembic upgrade head
sqlite3 /tmp/claude-1000/migcheck/prod_copy.db ".schema inbox_items" "SELECT status, COUNT(*) FROM documents GROUP BY status;"
DATABASE_PATH=/tmp/claude-1000/migcheck/prod_copy.db alembic downgrade -1
DATABASE_PATH=/tmp/claude-1000/migcheck/prod_copy.db alembic upgrade head
```

Expected:
- the `inbox_items` schema is printed;
- document status counts don't change;
- downgrade followed by re-upgrade succeeds.

If Plan A hasn't deployed yet, `upgrade head` also applies Plan A's migrations to the copy, which is fine.

- [ ] **Step 7: Run** `pytest -q`. Expected: all pass.

- [ ] **Step 8: Commit.**

```bash
git add app/models/document.py app/models/inbox_item.py app/models/__init__.py alembic/versions/c4b8e2d91f07_add_inbox_items_and_review_statuses.py tests/test_inbox_item_model.py
git commit -m "feat(inbox): add PENDING_REVIEW/DISCARDED statuses, TELEGRAM source, inbox_items table"
```

---

### Task 2: Model-readable check (+ webp)

**Files:** Modify `app/services/document_input.py`; Test `tests/test_document_input.py`

**Interfaces:**
- Produces: `is_model_readable(file_path: str) -> bool`. It is true only for what `build_content_block` can send to Claude: `.pdf .png .jpg .jpeg .webp`.
- Which files a channel *accepts* is decided separately, by Plan A's `media_kind_for(filename) != MediaKind.OTHER`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_document_input.py`:

```python
from app.services.document_input import build_content_block, is_model_readable


def test_is_model_readable():
    assert is_model_readable("/x/a.PDF")
    assert is_model_readable("/x/a.webp")
    assert not is_model_readable("/x/clip.mp4")
    assert not is_model_readable("/x/photo.heic")


def test_build_content_block_webp(tmp_path):
    path = tmp_path / "a.webp"
    path.write_bytes(b"RIFFxxxxWEBP")
    block = build_content_block(str(path))
    assert block["type"] == "image" and block["source"]["media_type"] == "image/webp"
```

- [ ] **Step 2: Run** `pytest tests/test_document_input.py -v`. Expected: FAIL (ImportError).

- [ ] **Step 3: Implement.** In `app/services/document_input.py`, add `".webp": "image/webp",` to `_IMAGE_MEDIA_TYPES`, then append:

```python
def is_model_readable(file_path: str) -> bool:
    """True if build_content_block can turn this file into a Claude block.
    Channels still store other media (HEIC, video); the classifier skips them."""
    suffix = Path(file_path).suffix.lower()
    return suffix == ".pdf" or suffix in _IMAGE_MEDIA_TYPES
```

- [ ] **Step 4: Run** `pytest tests/test_document_input.py -v`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add app/services/document_input.py tests/test_document_input.py
git commit -m "feat(ingestion): add is_model_readable and webp support"
```

---

### Task 3: Registry-driven domain + category classifier

**Files:**
- Create: `app/services/domain_classifier.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_domain_classifier.py`

**Interfaces:**
- Consumes: `implemented_domains()`; `DomainSpec.description`, `.categories[*].{value,label,description}` and `.infers_category`; `build_content_block` and `is_model_readable`; `strip_json_fences`.
- Produces:
  - `CONFIDENT_THRESHOLD = 0.8`
  - `@dataclass DomainSuggestion(domain: Optional[Domain], category: Optional[str], confidence: float, note: str)`
  - `build_system_prompt(domains) -> str`
  - `async suggest_domain_and_category(file_path, context_text=None, *, domains=None, client=None) -> DomainSuggestion`. It raises `DomainClassificationError` only when the model's response can't be parsed. For unreadable files, or answers outside the registry, it returns an empty suggestion (`None`/`None`/0.0). A domain with `infers_category=True` may come back with `category=None`.
  - Fixture `two_domains`, which replaces the registry with two fake specs:
    - "Money", registered on `Domain.FINANCIALS`, with `infers_category=True`, categories `bill` and `statement`, and no fields;
    - Plan A's `make_fake_spec()`, on `Domain.HOUSE`, with categories `manual` and `clip`, and a required `item_name` field for `manual`.
  - Fixture `inbox_documents_dir`.

- [ ] **Step 1: Add fixtures.** Append to `tests/conftest.py`:

```python
@pytest.fixture()
def two_domains(monkeypatch):
    """Registry replaced by two fake domains, so Plan B code is tested against
    the contract only: 'Money' (Domain.FINANCIALS, infers its category, no
    fields) and Plan A's fake House-like spec (Domain.HOUSE)."""
    import dataclasses

    from app.domains import registry
    from app.domains.base import CategorySpec, WikiSchema
    from app.models.domain import Domain
    from tests.domain_fakes import make_fake_spec

    house = make_fake_spec()
    money = dataclasses.replace(
        make_fake_spec(domain=Domain.FINANCIALS),
        label="Money", description="Money documents.", home_url="/money",
        categories=(CategorySpec("bill", "Bill", "A single bill."),
                    CategorySpec("statement", "Bank statement", "A list of transactions.")),
        fields=(), wiki=WikiSchema(), infers_category=True,
        document_url=lambda document: f"/money/{document.id}",
    )
    monkeypatch.setattr(registry, "_specs_cache", {money.domain: money, house.domain: house})
    return {"money": money, "house": house}


@pytest.fixture()
def inbox_documents_dir(tmp_path, monkeypatch):
    target = tmp_path / "documents"
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", target)
    return target
```

- [ ] **Step 2: Write the failing tests.** Create `tests/test_domain_classifier.py`:

```python
import json

import pytest

from app.domains.registry import implemented_domains
from app.models.domain import Domain
from app.services.domain_classifier import (
    DomainClassificationError, DomainSuggestion, build_system_prompt, suggest_domain_and_category,
)


class _Msg:
    def __init__(self, text):
        self.content = [type("C", (), {"text": text})()]


class _FakeClient:
    def __init__(self, text):
        self.calls = []
        outer = self

        class _Messages:
            async def create(self, **kwargs):
                outer.calls.append(kwargs)
                return _Msg(text)

        self.messages = _Messages()


def _answer(**data):
    return _FakeClient(json.dumps(data))


@pytest.fixture()
def pdf(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    return str(path)


def test_prompt_is_built_from_the_registry(two_domains):
    prompt = build_system_prompt(implemented_domains())
    for spec in two_domains.values():
        assert f'"{spec.domain.value}"' in prompt and spec.description in prompt
        for category in spec.categories:
            assert f'"{category.value}"' in prompt and category.description in prompt
    assert "may be null" in prompt   # Money infers its own category


@pytest.mark.asyncio
async def test_valid_answer(pdf, two_domains):
    client = _answer(domain="house", category="manual", confidence=0.93, reason="a manual")
    result = await suggest_domain_and_category(pdf, "Email subject: boiler", client=client)
    assert result == DomainSuggestion(Domain.HOUSE, "manual", 0.93, "a manual")
    user_content = client.calls[0]["messages"][0]["content"]
    assert any("Email subject: boiler" in b.get("text", "") for b in user_content)
    assert client.calls[0]["model"] == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_null_category_allowed_only_for_inferring_domains(pdf, two_domains):
    ok = await suggest_domain_and_category(pdf, client=_answer(domain="financials", category=None, confidence=0.9, reason="x"))
    assert ok.domain == Domain.FINANCIALS and ok.category is None
    bad = await suggest_domain_and_category(pdf, client=_answer(domain="house", category=None, confidence=0.9, reason="x"))
    assert bad.domain is None


@pytest.mark.asyncio
async def test_answer_outside_registry_becomes_empty(pdf, two_domains):
    wrong_cat = await suggest_domain_and_category(pdf, client=_answer(domain="house", category="bill", confidence=0.99, reason="x"))
    unknown_domain = await suggest_domain_and_category(pdf, client=_answer(domain="health", category="x", confidence=0.99, reason="x"))
    assert wrong_cat.domain is None and wrong_cat.confidence == 0.0
    assert unknown_domain.domain is None


@pytest.mark.asyncio
async def test_null_domain_keeps_reason(pdf, two_domains):
    result = await suggest_domain_and_category(pdf, client=_answer(domain=None, category=None, confidence=0.2, reason="a birthday card"))
    assert result.domain is None and result.note == "a birthday card"


@pytest.mark.asyncio
async def test_confidence_is_clamped(pdf, two_domains):
    result = await suggest_domain_and_category(pdf, client=_answer(domain="house", category="manual", confidence=7, reason="x"))
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_unreadable_file_skips_the_model(tmp_path, two_domains):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00")
    client = _FakeClient("unused")
    result = await suggest_domain_and_category(str(video), client=client)
    assert client.calls == [] and result.domain is None
    assert "can't be read automatically" in result.note


@pytest.mark.asyncio
async def test_garbage_response_raises(pdf, two_domains):
    with pytest.raises(DomainClassificationError):
        await suggest_domain_and_category(pdf, client=_FakeClient("not json"))
```

- [ ] **Step 3: Run** `pytest tests/test_domain_classifier.py -v`. Expected: FAIL (module not found).

- [ ] **Step 4: Implement.** Create `app/services/domain_classifier.py`:

```python
"""Suggests which domain + category an incoming document belongs to.

Registry-driven: the prompt is built from each registered DomainSpec's
description and its categories' descriptions, so a new domain becomes
classifiable the moment it registers -- nothing here names a domain. This
only ever SUGGESTS; a human approves every document in the Inbox."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Sequence

from anthropic import AsyncAnthropic

from app.config import settings
from app.domains.registry import implemented_domains
from app.models.domain import Domain
from app.services.document_input import build_content_block, is_model_readable
from app.services.json_utils import strip_json_fences

_MODEL = "claude-haiku-4-5-20251001"  # same cheap classifier model the Financials handler uses
CONFIDENT_THRESHOLD = 0.8


class DomainClassificationError(Exception):
    pass


@dataclass
class DomainSuggestion:
    domain: Optional[Domain]
    category: Optional[str]
    confidence: float
    note: str


def build_system_prompt(domains: Sequence) -> str:
    lines = [
        "You sort household documents into the area of family life they belong to.",
        "Choose exactly one domain and one of THAT domain's categories from this list:",
        "",
    ]
    for spec in domains:
        inferred = " (category may be null — this area works out its own category)" if spec.infers_category else ""
        lines.append(f'- domain "{spec.domain.value}" ({spec.label}): {spec.description}{inferred}')
        for category in spec.categories:
            lines.append(f'    - category "{category.value}" ({category.label}): {category.description}')
    lines += [
        "",
        "Respond with ONLY a JSON object:",
        '{"domain": "<domain id or null>", "category": "<category id or null>", '
        '"confidence": <number 0.0-1.0>, "reason": "<one short sentence>"}',
        "",
        "Use null for domain and category if the document fits none of them. "
        "confidence is how sure you are that the whole answer is right.",
    ]
    return "\n".join(lines)


def _empty(note: str) -> DomainSuggestion:
    return DomainSuggestion(domain=None, category=None, confidence=0.0, note=note)


async def suggest_domain_and_category(
    file_path: str,
    context_text: Optional[str] = None,
    *,
    domains: Optional[Sequence] = None,
    client: Optional[AsyncAnthropic] = None,
) -> DomainSuggestion:
    specs = list(domains) if domains is not None else implemented_domains()
    if not is_model_readable(file_path):
        return _empty("This file type can't be read automatically — please choose where it goes.")

    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    content: list[dict] = [build_content_block(file_path)]
    if context_text:
        content.append({"type": "text", "text": f"Context from the sender:\n{context_text}"})
    content.append({"type": "text", "text": "Classify this document as JSON."})

    message = await anthropic_client.messages.create(
        model=_MODEL,
        max_tokens=256,
        thinking={"type": "disabled"},
        system=build_system_prompt(specs),
        messages=[{"role": "user", "content": content}],
    )

    try:
        data = json.loads(strip_json_fences(message.content[0].text))
        domain_value = data.get("domain")
        category_value = data.get("category") or None
        confidence = min(max(float(data.get("confidence") or 0.0), 0.0), 1.0)
        reason = str(data.get("reason") or "")
    except (IndexError, AttributeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DomainClassificationError(f"Could not parse classifier response: {exc}") from exc

    if domain_value is None:
        return _empty(reason or "Didn't match any area.")
    spec = next((s for s in specs if s.domain.value == domain_value), None)
    valid_category = spec is not None and (
        category_value in {c.value for c in spec.categories}
        or (category_value is None and spec.infers_category)
    )
    if not valid_category:
        return _empty(f"Suggestion '{domain_value}/{category_value}' isn't a known area — please choose.")
    return DomainSuggestion(domain=spec.domain, category=category_value, confidence=confidence, note=reason)
```

- [ ] **Step 5: Run** `pytest tests/test_domain_classifier.py -v`. Expected: PASS (8 tests).

- [ ] **Step 6: Commit.**

```bash
git add app/services/domain_classifier.py tests/conftest.py tests/test_domain_classifier.py
git commit -m "feat(inbox): registry-driven domain+category classifier"
```

---

### Task 4: Inbox service: receive, list, approve, discard

**Files:** Create `app/services/inbox_service.py`; Test `tests/test_inbox_service.py`

**Interfaces:**
- Consumes:
  - from `app.services.ingestion`: `receive_file`, `validate_classification`, `finalize_document`, `IncomingFile`, `Classification`
  - from `app.domains.fields`: `InvalidClassification`
  - from the registry: `get_spec`, `is_implemented`
  - `wiki_store.append_log` and `WikiOperation.REVIEW`
  - from Task 3: `suggest_domain_and_category`, `DomainSuggestion`, `CONFIDENT_THRESHOLD`
  - from Task 1: `InboxItem`
- Produces:
  - `@dataclass InboxReceipt(document: Document, inbox_item: Optional[InboxItem], duplicate: bool)`
  - `async receive_document(session, incoming: IncomingFile, *, context_text: Optional[str], external_ref: Optional[str], classify=suggest_domain_and_category) -> InboxReceipt`
  - `item_for(session, document_id) -> Optional[InboxItem]`
  - `suggested_domain(item) -> Optional[Domain]`: the `Domain`, but only if the stored string is still a registered domain
  - `is_confident(item) -> bool`
  - `describe(domain: Optional[Domain], category: Optional[str]) -> Optional[str]`, for example `"Fake › Manual"`, or `"Money"` when the category is `None`
  - `@dataclass InboxEntry(item, document, confident: bool, suggestion_label: Optional[str])`
  - `entry_for(session, document_id) -> Optional[InboxEntry]`
  - `pending_entries(session) -> list[InboxEntry]`, oldest first
  - `recently_reviewed(session, limit=20) -> list[tuple[InboxItem, Document]]`
  - `class InboxError(Exception)`, raised when the document is not pending in the Inbox
  - `async approve(session, document_id, *, domain_value: str, category: Optional[str], fields: Mapping[str, str], reviewed_by: Optional[str]) -> Document`. Raises `InboxError`, or `InvalidClassification`, whose `.errors` the form shows.
  - `discard(session, document_id, *, reviewed_by) -> Document`
  - Log entries: `WikiOperation.REVIEW`, with descriptions starting `Approved ` or `Discarded `.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_inbox_service.py`:

```python
from pathlib import Path

import pytest
from sqlmodel import select

from app.domains.fields import InvalidClassification, load_fields
from app.models.document import DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.inbox_item import InboxItem
from app.models.wiki import WikiClaim, WikiLogEntry, WikiOperation, WikiPage
from app.services import inbox_service
from app.services.domain_classifier import DomainSuggestion
from app.services.ingestion import IncomingFile

pytestmark = pytest.mark.usefixtures("two_domains", "inbox_documents_dir")


def _classifier(suggestion):
    async def classify(file_path, context_text=None):
        return suggestion
    return classify


async def _receive(session, content=b"%PDF fake", suggestion=None, classify=None, filename="warranty.pdf"):
    return await inbox_service.receive_document(
        session,
        IncomingFile(filename=filename, content=content, source=DocumentSource.TELEGRAM, uploaded_by="Rute (Telegram)"),
        context_text="Telegram caption: boiler", external_ref="telegram:1:1",
        classify=classify or _classifier(suggestion or DomainSuggestion(Domain.HOUSE, "manual", 0.95, "a manual")),
    )


def _review_logs(session):
    return session.exec(select(WikiLogEntry).where(WikiLogEntry.operation == WikiOperation.REVIEW)).all()


@pytest.mark.asyncio
async def test_receive_creates_unfinalized_pending_review_document(session, two_domains):
    receipt = await _receive(session)

    doc = receipt.document
    assert not receipt.duplicate
    assert doc.status == DocumentStatus.PENDING_REVIEW
    assert doc.domain is None and doc.category is None
    assert doc.source == DocumentSource.TELEGRAM and doc.uploaded_by == "Rute (Telegram)"
    item = receipt.inbox_item
    assert (item.suggested_domain, item.suggested_category, item.confidence) == ("house", "manual", 0.95)
    # Nothing finalized, nothing in the wiki (not even a log line), before approval.
    assert two_domains["house"].handler.processed == []
    assert session.exec(select(WikiPage)).all() == []
    assert session.exec(select(WikiClaim)).all() == []
    assert session.exec(select(WikiLogEntry)).all() == []


@pytest.mark.asyncio
async def test_receive_duplicate_returns_existing(session):
    first = await _receive(session)
    second = await _receive(session)
    assert second.duplicate and second.document.id == first.document.id
    assert second.inbox_item.id == first.inbox_item.id
    assert len(session.exec(select(InboxItem)).all()) == 1


@pytest.mark.asyncio
async def test_classifier_failure_still_lands_in_inbox(session):
    async def boom(file_path, context_text=None):
        raise RuntimeError("credit balance too low")

    receipt = await _receive(session, classify=boom)
    assert receipt.document.status == DocumentStatus.PENDING_REVIEW
    assert receipt.inbox_item.suggested_domain is None
    assert "credit balance too low" in receipt.inbox_item.classifier_note


@pytest.mark.asyncio
async def test_pending_entries_confidence_and_labels(session):
    await _receive(session, content=b"a")
    await _receive(session, content=b"b", suggestion=DomainSuggestion(Domain.HOUSE, "clip", 0.4, "?"))
    await _receive(session, content=b"c", suggestion=DomainSuggestion(Domain.FINANCIALS, None, 0.9, "bill-ish"))
    entries = inbox_service.pending_entries(session)
    assert [e.confident for e in entries] == [True, False, True]
    assert [e.suggestion_label for e in entries] == ["Fake › Manual", "Fake › Clip", "Money"]


@pytest.mark.asyncio
async def test_stale_suggested_domain_is_ignored(session):
    receipt = await _receive(session)
    receipt.inbox_item.suggested_domain = "not_a_registered_domain"
    session.add(receipt.inbox_item)
    session.commit()
    entry = inbox_service.pending_entries(session)[0]
    assert entry.confident is False and entry.suggestion_label is None


@pytest.mark.asyncio
async def test_approve_finalizes_with_fields_and_logs_review(session, two_domains):
    receipt = await _receive(session)

    doc = await inbox_service.approve(
        session, receipt.document.id, domain_value="house", category="manual",
        fields={"item_name": "Boiler"}, reviewed_by="pedro@example.com",
    )

    assert doc.domain == Domain.HOUSE and doc.category == "manual"
    assert load_fields(doc) == {"item_name": "Boiler"}
    assert doc.status == DocumentStatus.PROCESSED
    assert two_domains["house"].handler.processed == [doc.id]
    item = inbox_service.item_for(session, doc.id)
    assert item.reviewed_by == "pedro@example.com" and item.reviewed_at is not None
    [log] = _review_logs(session)
    assert log.document_id == doc.id
    assert log.description.startswith("Approved warranty.pdf") and "Fake › Manual" in log.description


@pytest.mark.asyncio
async def test_approve_into_record_category_creates_record_with_file_attached(session, two_domains):
    from app.domains.entries import record_for_document

    receipt = await _receive(session, filename="service-invoice.pdf")
    doc = await inbox_service.approve(
        session, receipt.document.id, domain_value="house", category="visit",
        fields={"item_name": "Boiler", "visit_date": "2026-03-01"}, reviewed_by="pedro@example.com",
    )

    assert doc.domain == Domain.HOUSE and doc.category == "visit" and doc.status == DocumentStatus.PROCESSED
    assert load_fields(doc) == {}                           # fields live on the Record
    record = record_for_document(session, doc)
    assert record is not None and load_fields(record)["visit_date"] == "2026-03-01"
    [log] = _review_logs(session)
    assert "Fake › Visit" in log.description


@pytest.mark.asyncio
async def test_approve_with_inferred_category(session, two_domains):
    receipt = await _receive(session, suggestion=DomainSuggestion(Domain.FINANCIALS, None, 0.9, "x"))
    doc = await inbox_service.approve(session, receipt.document.id, domain_value="financials",
                                      category=None, fields={}, reviewed_by=None)
    assert doc.domain == Domain.FINANCIALS and doc.category is None
    assert two_domains["money"].handler.processed == [doc.id]


@pytest.mark.asyncio
async def test_approve_invalid_leaves_document_untouched(session, two_domains):
    receipt = await _receive(session)
    with pytest.raises(InvalidClassification) as missing_field:
        await inbox_service.approve(session, receipt.document.id, domain_value="house",
                                    category="manual", fields={}, reviewed_by=None)
    assert "item_name" in missing_field.value.errors
    with pytest.raises(InvalidClassification) as bad_domain:
        await inbox_service.approve(session, receipt.document.id, domain_value="health",
                                    category="x", fields={}, reviewed_by=None)
    assert "domain" in bad_domain.value.errors
    with pytest.raises(InvalidClassification) as bad_category:
        await inbox_service.approve(session, receipt.document.id, domain_value="house",
                                    category="bill", fields={}, reviewed_by=None)
    assert "category" in bad_category.value.errors

    session.refresh(receipt.document)
    assert receipt.document.status == DocumentStatus.PENDING_REVIEW and receipt.document.domain is None
    assert two_domains["house"].handler.processed == []
    assert _review_logs(session) == []
    assert inbox_service.item_for(session, receipt.document.id).reviewed_at is None


@pytest.mark.asyncio
async def test_approve_twice_is_refused(session):
    receipt = await _receive(session)
    await inbox_service.approve(session, receipt.document.id, domain_value="house", category="manual",
                                fields={"item_name": "Boiler"}, reviewed_by=None)
    with pytest.raises(inbox_service.InboxError):
        await inbox_service.approve(session, receipt.document.id, domain_value="house", category="manual",
                                    fields={"item_name": "Boiler"}, reviewed_by=None)


@pytest.mark.asyncio
async def test_discard_keeps_row_and_file_and_logs_review(session, two_domains):
    receipt = await _receive(session)
    doc = inbox_service.discard(session, receipt.document.id, reviewed_by="rute@example.com")

    assert doc.status == DocumentStatus.DISCARDED and doc.domain is None
    assert Path(doc.file_path).exists()
    assert two_domains["house"].handler.processed == []
    [log] = session.exec(select(WikiLogEntry)).all()
    assert log.operation == WikiOperation.REVIEW and log.description.startswith("Discarded warranty.pdf")
    assert inbox_service.pending_entries(session) == []
    assert inbox_service.recently_reviewed(session)[0][1].id == doc.id
    with pytest.raises(inbox_service.InboxError):
        inbox_service.discard(session, doc.id, reviewed_by=None)
```

- [ ] **Step 2: Run** `pytest tests/test_inbox_service.py -v`. Expected: FAIL (module not found).

- [ ] **Step 3: Implement.** Create `app/services/inbox_service.py`:

```python
"""The shared Inbox: every document arriving through an ingestion channel
(email, Telegram, any future one) enters via receive_document() and waits,
unfinalized (domain = NULL, status PENDING_REVIEW), until a human approves
or discards it. This is first-time filing only; corrections after filing
belong to Plan A's document edit flow.

Approval is the ONLY place a channel document is finalized: it validates
the human's choice with the ingestion core's validate_classification() and
then calls finalize_document() -- the same core a manual upload uses, which
runs the domain handler and the wiki Ingest. Nothing here touches the wiki
(other than the REVIEW log line), extraction or todos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional

from sqlmodel import Session, select

from app.domains.fields import InvalidClassification
from app.domains.registry import get_spec, is_implemented
from app.models.document import Document, DocumentStatus
from app.models.domain import Domain
from app.models.inbox_item import InboxItem
from app.models.wiki import WikiOperation
from app.services.domain_classifier import (
    CONFIDENT_THRESHOLD,
    DomainSuggestion,
    suggest_domain_and_category,
)
from app.services.ingestion import (
    Classification,
    IncomingFile,
    finalize_document,
    receive_file,
    validate_classification,
)
from app.services.wiki_store import append_log


class InboxError(Exception):
    """The document isn't waiting in the Inbox (unknown, or already handled)."""


@dataclass
class InboxReceipt:
    document: Document
    inbox_item: Optional[InboxItem]
    duplicate: bool


@dataclass
class InboxEntry:
    item: InboxItem
    document: Document
    confident: bool
    suggestion_label: Optional[str]


def item_for(session: Session, document_id: int) -> Optional[InboxItem]:
    return session.exec(select(InboxItem).where(InboxItem.document_id == document_id)).first()


async def receive_document(
    session: Session,
    incoming: IncomingFile,
    *,
    context_text: Optional[str],
    external_ref: Optional[str],
    classify=suggest_domain_and_category,
) -> InboxReceipt:
    received = receive_file(session, incoming, initial_status=DocumentStatus.PENDING_REVIEW)
    document = received.document
    if received.duplicate:
        return InboxReceipt(document=document, inbox_item=item_for(session, document.id), duplicate=True)

    try:
        suggestion = await classify(document.file_path, context_text)
    except Exception as exc:
        # Broad by design: a classifier failure (API down, credits, bad
        # response) must never lose the document -- it arrives without a
        # suggestion and a human chooses.
        suggestion = DomainSuggestion(None, None, 0.0, f"Automatic sorting failed: {exc}")

    item = InboxItem(
        document_id=document.id, context_text=context_text, external_ref=external_ref,
        suggested_domain=suggestion.domain.value if suggestion.domain is not None else None,
        suggested_category=suggestion.category,
        confidence=suggestion.confidence, classifier_note=suggestion.note,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return InboxReceipt(document=document, inbox_item=item, duplicate=False)


def suggested_domain(item: InboxItem) -> Optional[Domain]:
    """The suggestion as a registered Domain, or None if it no longer is one."""
    try:
        domain = Domain(item.suggested_domain) if item.suggested_domain else None
    except ValueError:
        return None
    return domain if is_implemented(domain) else None


def describe(domain: Optional[Domain], category: Optional[str]) -> Optional[str]:
    if not is_implemented(domain):
        return None
    spec = get_spec(domain)
    return f"{spec.label} › {spec.category_label(category)}" if category else spec.label


def is_confident(item: InboxItem) -> bool:
    domain = suggested_domain(item)
    if domain is None or (item.confidence or 0.0) < CONFIDENT_THRESHOLD:
        return False
    spec = get_spec(domain)
    if item.suggested_category is None:
        return spec.infers_category
    return item.suggested_category in {c.value for c in spec.categories}


def _entry(item: InboxItem, document: Document) -> InboxEntry:
    return InboxEntry(
        item=item, document=document, confident=is_confident(item),
        suggestion_label=describe(suggested_domain(item), item.suggested_category),
    )


def entry_for(session: Session, document_id: int) -> Optional[InboxEntry]:
    document = session.get(Document, document_id)
    item = item_for(session, document_id) if document else None
    return _entry(item, document) if item is not None else None


def pending_entries(session: Session) -> list[InboxEntry]:
    rows = session.exec(
        select(InboxItem, Document)
        .join(Document, InboxItem.document_id == Document.id)
        .where(Document.status == DocumentStatus.PENDING_REVIEW)
        .order_by(InboxItem.received_at, InboxItem.id)
    ).all()
    return [_entry(item, document) for item, document in rows]


def recently_reviewed(session: Session, limit: int = 20) -> list[tuple[InboxItem, Document]]:
    return list(session.exec(
        select(InboxItem, Document)
        .join(Document, InboxItem.document_id == Document.id)
        .where(InboxItem.reviewed_at.is_not(None))
        .order_by(InboxItem.reviewed_at.desc())
        .limit(limit)
    ).all())


def _pending(session: Session, document_id: int) -> tuple[Document, InboxItem]:
    document = session.get(Document, document_id)
    item = item_for(session, document_id) if document else None
    if document is None or item is None:
        raise InboxError("That document isn't in the Inbox.")
    if document.status != DocumentStatus.PENDING_REVIEW:
        raise InboxError("That document has already been handled.")
    return document, item


async def approve(
    session: Session,
    document_id: int,
    *,
    domain_value: str,
    category: Optional[str],
    fields: Mapping[str, str],
    reviewed_by: Optional[str],
) -> Document:
    document, item = _pending(session, document_id)
    try:
        domain = Domain(domain_value)
    except ValueError:
        raise InvalidClassification({"domain": "Choose an area"}) from None
    classification = Classification(domain=domain, category=category or None, fields=dict(fields))
    # Validate first so a rejected choice changes nothing (no review mark, no log).
    validate_classification(session, classification, document.filename)

    item.reviewed_by = reviewed_by
    item.reviewed_at = datetime.utcnow()
    session.add(item)
    append_log(
        session, WikiOperation.REVIEW,
        f"Approved {document.filename} as {describe(domain, classification.category)} "
        f"(via {document.source.value}, by {reviewed_by or 'unknown'})",
        document_id=document.id, commit=False,
    )
    session.commit()

    return await finalize_document(session, document, classification)


def discard(session: Session, document_id: int, *, reviewed_by: Optional[str]) -> Document:
    document, item = _pending(session, document_id)
    document.status = DocumentStatus.DISCARDED
    item.reviewed_by = reviewed_by
    item.reviewed_at = datetime.utcnow()
    session.add(document)
    session.add(item)
    append_log(
        session, WikiOperation.REVIEW,
        f"Discarded {document.filename} from the Inbox "
        f"(via {document.source.value}, by {reviewed_by or 'unknown'}); file kept",
        document_id=document.id, commit=False,
    )
    session.commit()
    session.refresh(document)
    return document
```

- [ ] **Step 4: Run** `pytest tests/test_inbox_service.py -v`. Expected: PASS (11 tests).

- [ ] **Step 5: Commit.**

```bash
git add app/services/inbox_service.py tests/test_inbox_service.py
git commit -m "feat(inbox): receive/approve/discard on top of the shared ingestion core"
```

---

### Task 5: Inbox page, domain fields at approval, nav, and `document_url` for pending docs

**Files:**
- Create: `app/routers/inbox.py`, `app/templates/inbox/{list.html,_entry.html,_fields.html,_result.html}`
- Modify: `app/main.py`, `app/templates/base.html`, `app/domains/registry.py`
- Test: `tests/test_inbox_router.py`, `tests/test_domain_registry.py`

**Interfaces:**
- Consumes:
  - Task 4;
  - from `app.domains.fields`: `build_form_fields`, `media_kind_for`, `file_url`, `InvalidClassification`;
  - `app.templating.templates`;
  - the template `domains/_field_inputs.html`.
- Produces these routes:
  - `GET /inbox`
  - `GET /inbox/{document_id}/fields?domain=&category=`
  - `POST /inbox/{document_id}/approve`, taking form fields `domain`, `category`, plus the domain's own field keys
  - `POST /inbox/{document_id}/discard`

  Approve and discard return `_result.html`, which htmx swaps in over the card (`outerHTML`). An invalid classification returns `_entry.html` with `errors` and status 422.
- Also produces: `registry.document_url(document)` returns `/inbox#inbox-<id>` for an unfinalized `PENDING_REVIEW` document.
- Also consumes `record_for_document` (`app.domains.entries`) and `registry.record_url`. After approval into a RECORD category, the result line links to the Record's page, not to the attachment.

Each of the following is a `UX-DEFAULT (pending Pedro)`:
- **Confident items are pre-selected.** An item counts as confident when the domain is known, the category is known or inferable, and confidence is at least 80%. Its card shows the domain's field inputs pre-filled (e.g. Item name, Room) and a green **Approve** button.
- **Uncertain items start empty.** The selects are blank, and any best guess appears as grey hint text: "Best guess: … — not sure."
- **Areas that work out their own category** (`infers_category`) show "Let the Hub work it out" as the empty option in the Type select.
- **Discard asks first:** "Discard this document? It will be kept on file but not filed anywhere."
- **Ordering:** oldest item first. A "Recently handled" list below shows the last 20.
- **Each card shows** the file name (opens the file in a new tab), the sender ("Rute (Telegram)" or an email address), when it arrived, and the caption or email subject.
- **RECORD categories** (for example House maintenance) appear in the Type list like any other category. Picking one shows the record's own fields (such as service date and notes), and the file becomes the record's attachment.
- **No editing after filing.** Approved documents leave the queue. Later corrections use Plan A's re-filing screens (`/documents/{id}/edit`, `/records/{id}/edit`), reached through the result line's "Open it" link or the domain tab.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_inbox_router.py`:

```python
import pytest

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.inbox_item import InboxItem

pytestmark = pytest.mark.usefixtures("two_domains")


def _pending(session, *, confident=True, content_hash="h1", filename="boiler.pdf",
             domain="house", category="manual"):
    document = Document(
        filename=filename, file_path=f"/tmp/{content_hash}.pdf", content_hash=content_hash,
        source=DocumentSource.TELEGRAM, status=DocumentStatus.PENDING_REVIEW, uploaded_by="Rute (Telegram)",
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    session.add(InboxItem(
        document_id=document.id, context_text="Telegram caption: new boiler",
        suggested_domain=domain, suggested_category=category,
        confidence=0.95 if confident else 0.3, classifier_note="a manual",
    ))
    session.commit()
    return document


def test_inbox_lists_pending_items_confident_prefilled(client, session):
    _pending(session, confident=True, content_hash="a", filename="boiler.pdf")
    _pending(session, confident=False, content_hash="b", filename="mystery.pdf", category="clip")

    html = client.get("/inbox").text

    assert "boiler.pdf" in html and "mystery.pdf" in html
    assert "Rute (Telegram)" in html and "new boiler" in html
    assert "Best guess: Fake › Clip" in html                  # the uncertain one
    assert 'value="house" selected' in html                   # the confident one, pre-filled
    assert 'name="item_name"' in html                         # domain field inputs rendered


def test_inbox_empty_state_and_nav(client):
    html = client.get("/inbox").text
    assert "Nothing waiting" in html
    assert 'href="/inbox"' in html


def test_fields_partial_follows_chosen_domain_and_category(client, session):
    document = _pending(session)
    money = client.get(f"/inbox/{document.id}/fields", params={"domain": "financials"}).text
    assert 'value="bill"' in money and 'value="statement"' in money and 'value="manual"' not in money
    assert "Let the Hub work it out" in money
    clip = client.get(f"/inbox/{document.id}/fields", params={"domain": "house", "category": "clip"}).text
    assert 'name="side"' in clip and 'name="item_name"' not in clip


def test_approve_finalizes_and_returns_result(client, session, two_domains):
    document = _pending(session)

    response = client.post(f"/inbox/{document.id}/approve",
                           data={"domain": "house", "category": "manual", "item_name": "Boiler"})

    assert response.status_code == 200
    assert "Fake › Manual" in response.text and 'href="/fake/documents/' in response.text
    assert two_domains["house"].handler.processed == [document.id]
    session.expire_all()
    assert session.get(Document, document.id).domain == Domain.HOUSE


def test_approve_into_record_category_links_to_the_record(client, session, two_domains):
    document = _pending(session, filename="service.pdf")
    fields = client.get(f"/inbox/{document.id}/fields", params={"domain": "house", "category": "visit"}).text
    assert 'name="visit_date"' in fields and 'name="item_name"' in fields

    response = client.post(f"/inbox/{document.id}/approve", data={
        "domain": "house", "category": "visit", "item_name": "Boiler", "visit_date": "2026-03-01",
    })

    assert response.status_code == 200
    assert "Fake › Visit" in response.text and 'href="/fake/records/' in response.text


def test_approve_shows_validation_errors(client, session, two_domains):
    document = _pending(session)

    response = client.post(f"/inbox/{document.id}/approve", data={"domain": "house", "category": "manual"})

    assert response.status_code == 422
    assert "Item is required" in response.text
    assert two_domains["house"].handler.processed == []

    bad_category = client.post(f"/inbox/{document.id}/approve", data={"domain": "house", "category": "bill"})
    assert bad_category.status_code == 422 and "Unknown Fake category" in bad_category.text


def test_discard(client, session, two_domains):
    document = _pending(session)
    response = client.post(f"/inbox/{document.id}/discard")
    assert response.status_code == 200 and "Discarded" in response.text
    session.expire_all()
    assert session.get(Document, document.id).status == DocumentStatus.DISCARDED
    assert two_domains["house"].handler.processed == []


def test_actions_on_missing_or_handled_document(client, session):
    assert client.post("/inbox/9999/discard").status_code == 404
    document = _pending(session)
    client.post(f"/inbox/{document.id}/discard")
    assert client.post(f"/inbox/{document.id}/discard").status_code == 409
```

Append to `tests/test_domain_registry.py`:

```python
def test_document_url_points_pending_review_documents_to_the_inbox(fake_domain):
    from app.models.document import DocumentStatus

    pending = Document(id=9, filename="a.pdf", file_path="/tmp/a.pdf", content_hash="h9",
                       source=DocumentSource.EMAIL, status=DocumentStatus.PENDING_REVIEW)
    discarded = Document(id=10, filename="b.pdf", file_path="/tmp/b.pdf", content_hash="h10",
                         source=DocumentSource.EMAIL, status=DocumentStatus.DISCARDED)
    assert registry.document_url(pending) == "/inbox#inbox-9"
    assert registry.document_url(discarded) is None
```

- [ ] **Step 2: Run** `pytest tests/test_inbox_router.py tests/test_domain_registry.py -v`. Expected: the inbox tests FAIL with 404, and the new registry test FAILS because it returns `None`.

- [ ] **Step 3: Extend `document_url`.** In `app/domains/registry.py`, change the import to `from app.models.document import Document, DocumentStatus` and replace the function:

```python
def document_url(document: Document) -> Optional[str]:
    """Where a document is viewed: its domain's page once finalized; its
    Inbox card while it waits for review; None otherwise (e.g. discarded)."""
    if is_implemented(document.domain):
        return get_spec(document.domain).document_url(document)
    if document.domain is None and document.status == DocumentStatus.PENDING_REVIEW:
        return f"/inbox#inbox-{document.id}"
    return None
```

- [ ] **Step 4: Implement the router.** Create `app/routers/inbox.py`. Each card is rendered from `inbox/_entry.html` with its own context, so the full page and the htmx error re-render share one card template.

```python
"""Routes for the shared, cross-domain Inbox (documents from email/Telegram
awaiting a human's first-time filing). All domain/category knowledge comes
from the registry; domain fields come from the generic fields layer -- no
domain is named here. DOCUMENT and RECORD categories are handled the same way
(finalize_document creates the Record). Re-filing after approval is Plan A's
/documents/{id}/edit and /records/{id}/edit."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from app.db import get_session
from app.domains.fields import InvalidClassification, build_form_fields, file_url, media_kind_for
from app.domains.entries import record_for_document
from app.domains.registry import document_url, get_spec, implemented_domains, is_implemented, record_url
from app.models.domain import Domain
from app.services import inbox_service
from app.templating import templates

router = APIRouter(prefix="/inbox", tags=["inbox"])

_SELECTOR_KEYS = ("domain", "category")


def _spec_for_value(domain_value: Optional[str]):
    try:
        domain = Domain(domain_value) if domain_value else None
    except ValueError:
        return None
    return get_spec(domain) if is_implemented(domain) else None


def _fields_context(session: Session, entry, domain_value: Optional[str], category: Optional[str],
                    values: dict, errors: dict) -> dict:
    spec = _spec_for_value(domain_value)
    if spec is None or category not in {c.value for c in spec.categories}:
        category = None   # changing the area resets the type
    form_fields = (
        build_form_fields(session, spec, category, values, errors,
                          media_kind=media_kind_for(entry.document.filename))
        if spec is not None and (category or spec.infers_category) else []
    )
    return {
        "entry": entry,
        "domains": implemented_domains(),
        "selected_spec": spec,
        "selected_domain": spec.domain.value if spec else "",
        "selected_category": category or "",
        "form_fields": form_fields,
        "errors": errors,
    }


def _card_context(session: Session, entry, *, domain_value=None, category=None,
                  values=None, errors=None) -> dict:
    if domain_value is None and entry.confident:  # confident => pre-filled
        domain_value = entry.item.suggested_domain
        category = entry.item.suggested_category
    return {
        "file_href": file_url(entry.document),
        **_fields_context(session, entry, domain_value, category, values or {}, errors or {}),
    }


def _entry_or_404(session: Session, document_id: int):
    entry = inbox_service.entry_for(session, document_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Not in the Inbox")
    return entry


@router.get("")
async def inbox_page(request: Request, session: Session = Depends(get_session)):
    card = templates.get_template("inbox/_entry.html")
    cards = [card.render(**_card_context(session, e)) for e in inbox_service.pending_entries(session)]
    recent = [
        {"item": item, "document": document,
         "label": inbox_service.describe(document.domain, document.category)}
        for item, document in inbox_service.recently_reviewed(session)
    ]
    return templates.TemplateResponse(request, "inbox/list.html", {"cards": cards, "recent": recent})


@router.get("/{document_id}/fields")
async def fields_partial(request: Request, document_id: int, domain: Optional[str] = None,
                         category: Optional[str] = None, session: Session = Depends(get_session)):
    entry = _entry_or_404(session, document_id)
    return templates.TemplateResponse(
        request, "inbox/_fields.html", _fields_context(session, entry, domain, category, {}, {})
    )


@router.post("/{document_id}/approve")
async def approve(request: Request, document_id: int, session: Session = Depends(get_session)):
    entry = _entry_or_404(session, document_id)
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    domain_value, category = form.get("domain", ""), form.get("category", "") or None
    fields = {k: v for k, v in form.items() if k not in _SELECTOR_KEYS}
    try:
        document = await inbox_service.approve(
            session, document_id, domain_value=domain_value, category=category, fields=fields,
            reviewed_by=getattr(request.state, "user_email", None),
        )
    except inbox_service.InboxError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidClassification as exc:
        ctx = _card_context(session, entry, domain_value=domain_value, category=category,
                            values=fields, errors=exc.errors)
        return templates.TemplateResponse(request, "inbox/_entry.html", ctx, status_code=422)

    record = record_for_document(session, document)  # RECORD category => link the record, not the attachment
    return templates.TemplateResponse(request, "inbox/_result.html", {
        "document": document, "discarded": False,
        "label": inbox_service.describe(document.domain, document.category),
        "document_href": record_url(record) if record is not None else document_url(document),
    })


@router.post("/{document_id}/discard")
async def discard(request: Request, document_id: int, session: Session = Depends(get_session)):
    _entry_or_404(session, document_id)
    try:
        document = inbox_service.discard(session, document_id,
                                         reviewed_by=getattr(request.state, "user_email", None))
    except inbox_service.InboxError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return templates.TemplateResponse(request, "inbox/_result.html", {
        "document": document, "discarded": True, "label": None, "document_href": None,
    })
```

- [ ] **Step 5: Templates.**

`app/templates/inbox/list.html`:

```html
{% extends "base.html" %}
{% block title %}Inbox{% endblock %}
{% block content %}
<h1>Inbox</h1>
<p>Documents sent by email or Telegram. Check where each one belongs, then approve it to file it.</p>
{% if cards %}
  {% for card in cards %}{{ card | safe }}{% endfor %}
{% else %}
  <p class="inbox-empty">Nothing waiting — you're all caught up.</p>
{% endif %}

{% if recent %}
<h2>Recently handled</h2>
<table class="review-table">
  <thead><tr><th>File</th><th>Outcome</th><th>By</th><th>When</th></tr></thead>
  <tbody>
  {% for row in recent %}
    <tr>
      <td>{{ row.document.filename }}</td>
      <td>
        {% if row.document.status.value == "discarded" %}Discarded
        {% else %}{{ row.label or "Filed" }}{% if row.document.status.value == "needs_attention" %} <span class="needs-attention">(needs attention)</span>{% endif %}
        {% endif %}
      </td>
      <td>{{ row.item.reviewed_by or "—" }}</td>
      <td>{{ row.item.reviewed_at.strftime("%d %b %H:%M") }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}
{% endblock %}
```

`app/templates/inbox/_entry.html`:

```html
<div class="inbox-card{% if entry.confident %} confident{% endif %}" id="inbox-{{ entry.document.id }}">
  <div class="inbox-meta">
    <a href="{{ file_href }}" target="_blank"><strong>{{ entry.document.filename }}</strong></a>
    <span>from {{ entry.document.uploaded_by or "unknown sender" }}</span>
    <span>{{ entry.item.received_at.strftime("%d %b %H:%M") }}</span>
  </div>
  {% if entry.item.context_text %}<p class="inbox-context">{{ entry.item.context_text }}</p>{% endif %}
  {% if not entry.confident %}
    <p class="inbox-hint">
      {% if entry.suggestion_label %}Best guess: {{ entry.suggestion_label }} — not sure.{% else %}Not sure where this belongs.{% endif %}
      {% if entry.item.classifier_note %}<em>{{ entry.item.classifier_note }}</em>{% endif %}
    </p>
  {% endif %}
  <form hx-post="/inbox/{{ entry.document.id }}/approve" hx-target="#inbox-{{ entry.document.id }}" hx-swap="outerHTML">
    <div id="fields-{{ entry.document.id }}" class="inbox-fields">
      {% include "inbox/_fields.html" %}
    </div>
    <button type="submit" class="inbox-approve">Approve</button>
    <button type="button" class="inbox-discard"
            hx-post="/inbox/{{ entry.document.id }}/discard" hx-target="#inbox-{{ entry.document.id }}" hx-swap="outerHTML"
            hx-confirm="Discard this document? It will be kept on file but not filed anywhere.">Discard</button>
  </form>
</div>
```

`app/templates/inbox/_fields.html`. The Area and Type selects re-fetch this partial, which is how the domain's own fields appear. The field inputs themselves come from Plan A's generic partial.

```html
<label>Area
  <select name="domain" required
          hx-get="/inbox/{{ entry.document.id }}/fields" hx-target="#fields-{{ entry.document.id }}"
          hx-include="closest form" hx-trigger="change">
    <option value="" {% if not selected_domain %}selected{% endif %}>Choose…</option>
    {% for spec in domains %}
      <option value="{{ spec.domain.value }}" {% if spec.domain.value == selected_domain %}selected{% endif %}>{{ spec.label }}</option>
    {% endfor %}
  </select>
</label>
{% if errors.domain %}<span class="needs-attention">{{ errors.domain }}</span>{% endif %}
<label>Type
  <select name="category" {% if not (selected_spec and selected_spec.infers_category) %}required{% endif %}
          hx-get="/inbox/{{ entry.document.id }}/fields" hx-target="#fields-{{ entry.document.id }}"
          hx-include="closest form" hx-trigger="change">
    <option value="" {% if not selected_category %}selected{% endif %}>
      {% if selected_spec and selected_spec.infers_category %}Let the Hub work it out{% else %}Choose…{% endif %}
    </option>
    {% if selected_spec %}
      {% for category in selected_spec.categories %}
        <option value="{{ category.value }}" {% if category.value == selected_category %}selected{% endif %}>{{ category.label }}</option>
      {% endfor %}
    {% endif %}
  </select>
</label>
{% if errors.category %}<span class="needs-attention">{{ errors.category }}</span>{% endif %}
{% if errors.file %}<span class="needs-attention">{{ errors.file }}</span>{% endif %}
{% if form_fields %}{% include "domains/_field_inputs.html" %}{% endif %}
```

`app/templates/inbox/_result.html`:

```html
<div class="inbox-card done" id="inbox-{{ document.id }}">
  <strong>{{ document.filename }}</strong> —
  {% if discarded %}
    Discarded (kept on file).
  {% elif document.status.value == "needs_attention" %}
    filed as {{ label }}, but processing had a problem: <span class="needs-attention">{{ document.failure_reason }}</span>
    {% if document_href %}<a href="{{ document_href }}">Open it</a>{% endif %}
  {% else %}
    filed as {{ label }}. {% if document_href %}<a href="{{ document_href }}">Open it</a>{% endif %}
  {% endif %}
</div>
```

In `app/templates/base.html`, add `<a href="/inbox">Inbox</a>` directly after `<a href="/">Overview</a>`, before the registry-driven domain groups. Then add these styles to the `<style>` block:

```css
    .inbox-card { border: 1px solid #e2e2e2; border-radius: 8px; padding: 12px 14px; margin-bottom: 12px; }
    .inbox-card.confident { border-left: 4px solid #1f8a4c; }
    .inbox-card.done { background: #fafafa; color: #555; }
    .inbox-meta { display: flex; gap: 14px; align-items: baseline; font-size: 14px; }
    .inbox-meta span { color: #888; font-size: 12px; }
    .inbox-context, .inbox-hint { font-size: 13px; color: #555; margin: 6px 0; }
    .inbox-card form { margin-top: 8px; }
    .inbox-fields { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin-bottom: 8px; }
    .inbox-approve { background: #1f8a4c; color: #fff; border: none; border-radius: 5px; padding: 5px 14px; cursor: pointer; }
    .inbox-discard { background: none; border: 1px solid #ccc; border-radius: 5px; padding: 5px 12px; cursor: pointer; color: #777; }
    .inbox-empty { color: #888; }
```

In `app/main.py`, add `inbox` to the routers import line, keeping whatever Plan A put there. Then add `app.include_router(inbox.router)`.

- [ ] **Step 6: Run** `pytest tests/test_inbox_router.py tests/test_domain_registry.py -v`, then `pytest -q`. Expected: all PASS. If a `tests/test_main.py` nav assertion lists the exact links, add Inbox to it.

- [ ] **Step 7: Look at it.**
  1. Start the app locally with a scratch `DATABASE_PATH`, using the `run` skill or `uvicorn app.main:app --port 9001`.
  2. In a Python shell, add two pending items using `inbox_service.receive_document` with a stub classifier.
  3. Open `/inbox` in the browser and check that:
     - the cards render;
     - changing Area reloads the Type select and the domain's fields;
     - a missing required field shows its error next to the field;
     - Approve replaces the card with the result line;
     - Discard asks for confirmation.

  Known cosmetic limit: `_field_inputs.html` uses ids like `f-item_name`, so the same ids repeat across cards. Labels still work within each card, but clicking one can focus the first card's input. A fix in Plan A is suggested in the report.

- [ ] **Step 8: Commit.**

```bash
git add app/routers/inbox.py app/templates/inbox app/templates/base.html app/main.py app/domains/registry.py tests/test_inbox_router.py tests/test_domain_registry.py tests/test_main.py
git commit -m "feat(inbox): top-level Inbox with domain fields at approval; pending docs link to their Inbox card"
```

---

### Task 6: Channel settings (secrets handled like the rest of `.env`)

**Files:** Modify `app/config.py` and `.env.example`; Test `tests/test_config_channels.py`

**Interfaces:**
- Produces on `settings`:
  - `HUB_IMAP_HOST`, `HUB_IMAP_PORT: int` (default 993), `HUB_IMAP_USER`, `HUB_IMAP_PASSWORD`
  - `HUB_TELEGRAM_BOT_TOKEN`
  - `PUBLIC_BASE_URL` (default `https://hub.cdafamily.casa`)
  - Lazily-parsed properties: `imap_configured -> bool`, `imap_allowed_senders -> frozenset[str]`, `telegram_allowed_users -> dict[int, str]`
- Also produces pure parsers `parse_sender_allowlist(raw)` and `parse_telegram_users(raw)`. The latter raises `ValueError` naming the bad entry.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_config_channels.py`:

```python
import pytest

from app.config import parse_sender_allowlist, parse_telegram_users, settings


def test_parse_sender_allowlist_normalises():
    assert parse_sender_allowlist(" Pedro@Example.com, rute@example.com ,,") == frozenset(
        {"pedro@example.com", "rute@example.com"}
    )
    assert parse_sender_allowlist("") == frozenset()


def test_parse_telegram_users():
    assert parse_telegram_users("111:Pedro, 222:Rute") == {111: "Pedro", 222: "Rute"}
    assert parse_telegram_users("") == {}


@pytest.mark.parametrize("bad", ["abc:Pedro", "111", "111:"])
def test_parse_telegram_users_rejects_bad_entries(bad):
    with pytest.raises(ValueError, match="HUB_TELEGRAM_ALLOWED_USERS"):
        parse_telegram_users(bad)


def test_channel_settings_have_safe_types_and_defaults():
    assert isinstance(settings.imap_configured, bool)
    assert isinstance(settings.HUB_IMAP_PORT, int)
    assert settings.PUBLIC_BASE_URL.startswith("https://")
```

- [ ] **Step 2: Run** `pytest tests/test_config_channels.py -v`. Expected: FAIL (ImportError).

- [ ] **Step 3: Implement.** In `app/config.py`, add the parsers above `class Settings`:

```python
def parse_sender_allowlist(raw: str) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def parse_telegram_users(raw: str) -> dict[int, str]:
    """"111:Pedro,222:Rute" -> {111: "Pedro", 222: "Rute"}."""
    users: dict[int, str] = {}
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        user_id, _, name = part.partition(":")
        if not user_id.strip().isdigit() or not name.strip():
            raise ValueError(f"HUB_TELEGRAM_ALLOWED_USERS: bad entry {part!r} (expected <id>:<name>)")
        users[int(user_id)] = name.strip()
    return users
```

Then add these inside `Settings`:

```python
    # --- Ingestion channels (all optional; the web app never needs them) ---
    HUB_IMAP_HOST: str = os.environ.get("HUB_IMAP_HOST") or ""
    HUB_IMAP_PORT: int = int(os.environ.get("HUB_IMAP_PORT") or 993)
    HUB_IMAP_USER: str = os.environ.get("HUB_IMAP_USER") or ""
    HUB_IMAP_PASSWORD: str = os.environ.get("HUB_IMAP_PASSWORD") or ""
    HUB_TELEGRAM_BOT_TOKEN: str = os.environ.get("HUB_TELEGRAM_BOT_TOKEN") or ""
    PUBLIC_BASE_URL: str = os.environ.get("PUBLIC_BASE_URL") or "https://hub.cdafamily.casa"

    @property
    def imap_configured(self) -> bool:
        return bool(self.HUB_IMAP_HOST and self.HUB_IMAP_USER and self.HUB_IMAP_PASSWORD)

    @property
    def imap_allowed_senders(self) -> frozenset[str]:
        return parse_sender_allowlist(os.environ.get("HUB_IMAP_ALLOWED_SENDERS") or "")

    @property
    def telegram_allowed_users(self) -> dict[int, str]:
        return parse_telegram_users(os.environ.get("HUB_TELEGRAM_ALLOWED_USERS") or "")
```

Append to `.env.example`:

```
# Ingestion channels (optional — leave empty to keep a channel switched off)
HUB_IMAP_HOST=imap.gmail.com
HUB_IMAP_PORT=993
HUB_IMAP_USER=your-hub-mailbox@gmail.com
HUB_IMAP_PASSWORD=your_16_letter_app_password
HUB_IMAP_ALLOWED_SENDERS=person1@example.com,person2@example.com
HUB_TELEGRAM_BOT_TOKEN=123456:your_bot_token_from_botfather
HUB_TELEGRAM_ALLOWED_USERS=111111111:Name1,222222222:Name2
PUBLIC_BASE_URL=https://hub.cdafamily.casa
```

- [ ] **Step 4: Run** `pytest tests/test_config_channels.py -v`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add app/config.py .env.example tests/test_config_channels.py
git commit -m "feat(channels): optional IMAP/Telegram settings with lazy allowlist parsing"
```

---

### Task 7: Email channel: parser + IMAP poller

**Files:**
- Create:
  - `app/channels/__init__.py`, containing only this docstring: `"""Ingestion channel adapters. Each turns an external message into ingestion.IncomingFile objects and calls inbox_service.receive_document -- nothing else."""`
  - `app/channels/email_parsing.py`
  - `app/channels/email_poller.py`
- Test: `tests/test_email_parsing.py`, `tests/test_email_poller.py`

**Interfaces:**
- Consumes: `media_kind_for` and `MediaKind`; `IncomingFile`; `inbox_service.receive_document` and `InboxReceipt`; settings.
- Produces, in `email_parsing`:
  - `@dataclass EmailAttachment(filename, content)`
  - `@dataclass ParsedEmail(sender, subject, message_id, context_text, attachments: list[EmailAttachment])`
  - `parse_email(raw: bytes) -> ParsedEmail`
  - `MIN_IMAGE_BYTES = 15_000` and `MAX_FILE_BYTES = 25 * 1024 * 1024`
- Produces, in `email_poller`:
  - folder names `PROCESSED_FOLDER="Hub-Processed"`, `IGNORED_FOLDER="Hub-Ignored"`, `FAILED_FOLDER="Hub-Failed"`
  - `MAX_MESSAGES_PER_RUN = 20`
  - the `Mailbox` Protocol and `ImapMailbox`
  - `PollReport`
  - `async poll_once(mailbox, session_factory, allowed_senders, receive=receive_document) -> PollReport`
  - `main() -> int`

Behaviour. Each point is a `UX-DEFAULT (pending Pedro)`:
- **Attachments only.** Each accepted attachment becomes its own Inbox item. "Accepted" means `media_kind_for` isn't `OTHER`: PDF, image or video.
- **Emails with no usable attachment** go to `Hub-Ignored`. A link-only e-invoice isn't fetched.
- **Small images and large files are skipped.** Images under 15 KB are usually email-signature logos. Files over 25 MB are skipped too.
- **Forwarded-as-attachment emails work**, because the parser walks nested messages.
- **Unknown senders** go to `Hub-Ignored` without an LLM call, and the sender is logged.
- **Errors** move the email to `Hub-Failed`, and the run continues. Re-running is safe thanks to content-hash dedup.

- [ ] **Step 1: Write the failing parser tests.** Create `tests/test_email_parsing.py`:

```python
from email.message import EmailMessage

from app.channels.email_parsing import MIN_IMAGE_BYTES, parse_email


def _mail(sender="Rute <Rute@Example.com>", subject="Boiler warranty", body="See attached"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "hub@example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = "<abc@mail>"
    msg.set_content(body)
    return msg


def test_extracts_pdf_attachment_and_context():
    msg = _mail()
    msg.add_attachment(b"%PDF-1.4 data", maintype="application", subtype="pdf", filename="warranty.pdf")
    parsed = parse_email(msg.as_bytes())
    assert parsed.sender == "rute@example.com" and parsed.subject == "Boiler warranty"
    assert parsed.message_id == "<abc@mail>"
    assert "Boiler warranty" in parsed.context_text and "See attached" in parsed.context_text
    assert [(a.filename, a.content) for a in parsed.attachments] == [("warranty.pdf", b"%PDF-1.4 data")]


def test_skips_tiny_images_and_unsupported_types():
    msg = _mail()
    msg.add_attachment(b"x" * 100, maintype="image", subtype="png", filename="logo.png")
    msg.add_attachment(b"MZ...", maintype="application", subtype="octet-stream", filename="setup.exe")
    msg.add_attachment(b"y" * (MIN_IMAGE_BYTES + 1), maintype="image", subtype="jpeg", filename="photo.JPG")
    assert [a.filename for a in parse_email(msg.as_bytes()).attachments] == ["photo.JPG"]


def test_finds_attachments_inside_forwarded_message():
    inner = _mail(sender="shop@example.com", subject="Your invoice")
    inner.add_attachment(b"%PDF inner", maintype="application", subtype="pdf", filename="invoice.pdf")
    outer = _mail(subject="Fwd: Your invoice")
    outer.add_attachment(inner)
    parsed = parse_email(outer.as_bytes())
    assert [a.filename for a in parsed.attachments] == ["invoice.pdf"]
    assert parsed.sender == "rute@example.com"


def test_no_attachments():
    assert parse_email(_mail().as_bytes()).attachments == []
```

- [ ] **Step 2: Run** `pytest tests/test_email_parsing.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement** `app/channels/email_parsing.py`:

```python
"""Pure parsing of one raw email into the attachments the Hub should ingest."""

from __future__ import annotations

import email
import email.policy
from dataclasses import dataclass, field
from email.utils import parseaddr

from app.domains.base import MediaKind
from app.domains.fields import media_kind_for

MIN_IMAGE_BYTES = 15_000            # smaller images are signature logos / tracking pixels
MAX_FILE_BYTES = 25 * 1024 * 1024
_CONTEXT_BODY_CHARS = 500


@dataclass
class EmailAttachment:
    filename: str
    content: bytes


@dataclass
class ParsedEmail:
    sender: str
    subject: str
    message_id: str
    context_text: str
    attachments: list[EmailAttachment] = field(default_factory=list)


def parse_email(raw: bytes) -> ParsedEmail:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = parseaddr(str(msg.get("From", "")))[1].strip().lower()
    subject = str(msg.get("Subject", "")).strip()
    message_id = str(msg.get("Message-ID", "")).strip()

    body_part = msg.get_body(preferencelist=("plain",))
    body = body_part.get_content().strip() if body_part is not None else ""
    context_text = f"Email subject: {subject}\n{body[:_CONTEXT_BODY_CHARS]}".strip()

    attachments: list[EmailAttachment] = []
    for part in msg.walk():  # also descends into forwarded message/rfc822 parts
        if part.is_multipart():
            continue
        filename = part.get_filename()
        if not filename:
            continue
        kind = media_kind_for(filename)
        if kind == MediaKind.OTHER:
            continue
        content = part.get_payload(decode=True) or b""
        if len(content) > MAX_FILE_BYTES:
            continue
        if kind == MediaKind.IMAGE and len(content) < MIN_IMAGE_BYTES:
            continue
        attachments.append(EmailAttachment(filename=filename, content=content))

    return ParsedEmail(sender=sender, subject=subject, message_id=message_id,
                       context_text=context_text, attachments=attachments)
```

- [ ] **Step 4: Run** `pytest tests/test_email_parsing.py -v`. Expected: PASS.

- [ ] **Step 5: Write the failing poller tests.** Create `tests/test_email_poller.py`:

```python
from email.message import EmailMessage

import pytest
from sqlmodel import Session

from app.channels import email_poller
from app.channels.email_poller import FAILED_FOLDER, IGNORED_FOLDER, PROCESSED_FOLDER, poll_once
from app.models.document import DocumentSource
from app.services.inbox_service import InboxReceipt

ALLOWED = frozenset({"rute@example.com"})


def _raw(sender="rute@example.com", with_pdf=True):
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = "Docs"
    msg["Message-ID"] = "<m1@x>"
    msg.set_content("hello")
    if with_pdf:
        msg.add_attachment(b"%PDF a", maintype="application", subtype="pdf", filename="a.pdf")
        msg.add_attachment(b"%PDF b", maintype="application", subtype="pdf", filename="b.pdf")
    return msg.as_bytes()


class FakeMailbox:
    def __init__(self, messages):
        self.messages = dict(messages)
        self.moves = []
        self.folders = None

    def ensure_folders(self, names):
        self.folders = list(names)

    def list_uids(self):
        return list(self.messages)

    def fetch(self, uid):
        return self.messages[uid]

    def move(self, uid, folder):
        self.moves.append((uid, folder))

    def close(self):
        pass


@pytest.fixture()
def recorder():
    calls = []

    async def fake_receive(session, incoming, **kwargs):
        calls.append((incoming, kwargs))
        return InboxReceipt(document=None, inbox_item=None, duplicate=False)

    return calls, fake_receive


@pytest.mark.asyncio
async def test_allowed_sender_each_attachment_received_then_processed(engine, recorder):
    calls, fake_receive = recorder
    mailbox = FakeMailbox({b"1": _raw()})

    report = await poll_once(mailbox, lambda: Session(engine), ALLOWED, receive=fake_receive)

    assert [c[0].filename for c in calls] == ["a.pdf", "b.pdf"]
    incoming, kwargs = calls[0]
    assert incoming.source == DocumentSource.EMAIL and incoming.uploaded_by == "rute@example.com"
    assert kwargs["external_ref"] == "email:<m1@x>#0" and "Docs" in kwargs["context_text"]
    assert mailbox.moves == [(b"1", PROCESSED_FOLDER)]
    assert report.processed == 1 and report.documents_created == 2
    assert set(mailbox.folders) == {PROCESSED_FOLDER, IGNORED_FOLDER, FAILED_FOLDER}


@pytest.mark.asyncio
async def test_unknown_sender_is_ignored_without_llm(engine, recorder):
    calls, fake_receive = recorder
    mailbox = FakeMailbox({b"1": _raw(sender="spam@evil.test")})
    report = await poll_once(mailbox, lambda: Session(engine), ALLOWED, receive=fake_receive)
    assert calls == [] and mailbox.moves == [(b"1", IGNORED_FOLDER)] and report.ignored == 1


@pytest.mark.asyncio
async def test_no_attachment_is_ignored(engine, recorder):
    _, fake_receive = recorder
    mailbox = FakeMailbox({b"1": _raw(with_pdf=False)})
    await poll_once(mailbox, lambda: Session(engine), ALLOWED, receive=fake_receive)
    assert mailbox.moves == [(b"1", IGNORED_FOLDER)]


@pytest.mark.asyncio
async def test_failure_moves_to_failed_and_continues(engine):
    async def exploding_receive(session, incoming, **kwargs):
        if incoming.filename == "a.pdf":
            raise RuntimeError("disk full")
        return InboxReceipt(document=None, inbox_item=None, duplicate=False)

    mailbox = FakeMailbox({b"1": _raw(), b"2": _raw(with_pdf=False)})
    report = await poll_once(mailbox, lambda: Session(engine), ALLOWED, receive=exploding_receive)
    assert mailbox.moves == [(b"1", FAILED_FOLDER), (b"2", IGNORED_FOLDER)] and report.failed == 1


@pytest.mark.asyncio
async def test_caps_messages_per_run(engine, recorder, monkeypatch):
    _, fake_receive = recorder
    monkeypatch.setattr(email_poller, "MAX_MESSAGES_PER_RUN", 1)
    mailbox = FakeMailbox({b"1": _raw(), b"2": _raw()})
    await poll_once(mailbox, lambda: Session(engine), ALLOWED, receive=fake_receive)
    assert [m[0] for m in mailbox.moves] == [b"1"]


def test_main_exits_cleanly_when_unconfigured(monkeypatch, capsys):
    monkeypatch.setattr(email_poller.settings, "HUB_IMAP_HOST", "")
    assert email_poller.main() == 0
    assert "not configured" in capsys.readouterr().out
```

- [ ] **Step 6: Run** `pytest tests/test_email_poller.py -v`. Expected: FAIL.

- [ ] **Step 7: Implement** `app/channels/email_poller.py`:

```python
"""Email ingestion channel: polls the Hub's dedicated mailbox over IMAP and
drops every attachment from an allow-listed sender into the Inbox.

Run once per systemd timer tick (deploy/systemd/home-hub-mailpoll.timer):
    python -m app.channels.email_poller
Handled mail is MOVED out of INBOX into Hub-Processed / Hub-Ignored /
Hub-Failed -- never deleted, and never tracked via read/unread flags."""

from __future__ import annotations

import asyncio
import imaplib
import logging
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

from sqlmodel import Session

from app.channels.email_parsing import parse_email
from app.config import settings
from app.models.document import DocumentSource
from app.services.inbox_service import receive_document
from app.services.ingestion import IncomingFile

logger = logging.getLogger("home_hub.email_poller")

PROCESSED_FOLDER = "Hub-Processed"
IGNORED_FOLDER = "Hub-Ignored"
FAILED_FOLDER = "Hub-Failed"
MAX_MESSAGES_PER_RUN = 20


class Mailbox(Protocol):
    def ensure_folders(self, names: Iterable[str]) -> None: ...
    def list_uids(self) -> list[bytes]: ...
    def fetch(self, uid: bytes) -> bytes: ...
    def move(self, uid: bytes, folder: str) -> None: ...
    def close(self) -> None: ...


class ImapMailbox:
    """Thin imaplib wrapper. Only INBOX is read."""

    def __init__(self, host: str, port: int, user: str, password: str):
        self._imap = imaplib.IMAP4_SSL(host, port)
        self._imap.login(user, password)
        _, caps = self._imap.capability()
        self._can_move = b"MOVE" in (caps[0] or b"").upper().split()
        self._imap.select("INBOX")

    def ensure_folders(self, names: Iterable[str]) -> None:
        for name in names:
            self._imap.create(name)  # answers NO if it already exists -- harmless

    def list_uids(self) -> list[bytes]:
        _, data = self._imap.uid("SEARCH", None, "ALL")
        return (data[0] or b"").split()

    def fetch(self, uid: bytes) -> bytes:
        _, data = self._imap.uid("FETCH", uid, "(BODY.PEEK[])")
        return data[0][1]

    def move(self, uid: bytes, folder: str) -> None:
        if self._can_move:
            self._imap.uid("MOVE", uid, folder)
            return
        self._imap.uid("COPY", uid, folder)
        self._imap.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
        self._imap.expunge()

    def close(self) -> None:
        try:
            self._imap.logout()
        except Exception:
            pass


@dataclass
class PollReport:
    processed: int = 0
    ignored: int = 0
    failed: int = 0
    documents_created: int = 0


async def poll_once(
    mailbox: Mailbox,
    session_factory: Callable[[], Session],
    allowed_senders: frozenset[str],
    receive=receive_document,
) -> PollReport:
    report = PollReport()
    mailbox.ensure_folders([PROCESSED_FOLDER, IGNORED_FOLDER, FAILED_FOLDER])

    for uid in mailbox.list_uids()[:MAX_MESSAGES_PER_RUN]:
        try:
            parsed = parse_email(mailbox.fetch(uid))
        except Exception:
            logger.exception("could not read message uid=%s", uid)
            mailbox.move(uid, FAILED_FOLDER)
            report.failed += 1
            continue

        if parsed.sender not in allowed_senders:
            logger.info("ignoring mail from non-allow-listed sender %s", parsed.sender)
            mailbox.move(uid, IGNORED_FOLDER)
            report.ignored += 1
            continue
        if not parsed.attachments:
            logger.info("ignoring mail without usable attachments from %s: %s", parsed.sender, parsed.subject)
            mailbox.move(uid, IGNORED_FOLDER)
            report.ignored += 1
            continue

        try:
            with session_factory() as session:
                for index, attachment in enumerate(parsed.attachments):
                    receipt = await receive(
                        session,
                        IncomingFile(filename=attachment.filename, content=attachment.content,
                                     source=DocumentSource.EMAIL, uploaded_by=parsed.sender),
                        context_text=parsed.context_text,
                        external_ref=f"email:{parsed.message_id}#{index}",
                    )
                    if not receipt.duplicate:
                        report.documents_created += 1
        except Exception:
            # Content-hash dedup makes a later retry (moving the mail back to
            # INBOX) safe even if some attachments were already stored.
            logger.exception("failed to ingest mail uid=%s from %s", uid, parsed.sender)
            mailbox.move(uid, FAILED_FOLDER)
            report.failed += 1
            continue

        mailbox.move(uid, PROCESSED_FOLDER)
        report.processed += 1

    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not settings.imap_configured:
        print("Hub mailbox not configured (HUB_IMAP_* unset) — nothing to do.")
        return 0

    from app.db import engine

    mailbox = ImapMailbox(settings.HUB_IMAP_HOST, settings.HUB_IMAP_PORT,
                          settings.HUB_IMAP_USER, settings.HUB_IMAP_PASSWORD)
    try:
        report = asyncio.run(poll_once(mailbox, lambda: Session(engine), settings.imap_allowed_senders))
    finally:
        mailbox.close()
    print(f"mail poll: {report}")
    return 1 if report.failed else 0  # non-zero => unit shows "failed" (surfaced by deploy + runbook)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8: Run** `pytest tests/test_email_parsing.py tests/test_email_poller.py -v`. Expected: PASS.

- [ ] **Step 9: Commit.**

```bash
git add app/channels/__init__.py app/channels/email_parsing.py app/channels/email_poller.py tests/test_email_parsing.py tests/test_email_poller.py
git commit -m "feat(channels): IMAP email poller feeding the Inbox"
```

---

### Task 8: Telegram channel: dedicated Hub bot

**Files:**
- Create: `app/channels/telegram_bot.py`
- Modify: `requirements.txt`. Add `python-telegram-bot>=21.3`, the same version floor Recipes uses.
- Test: `tests/test_telegram_bot.py`

**Interfaces:**
- Consumes: from `inbox_service`, `receive_document`, `InboxReceipt`, `is_confident`, `describe` and `suggested_domain`; `IncomingFile`; `media_kind_for` and `MediaKind`; settings.
- Produces:
  - `MAX_BOT_DOWNLOAD_BYTES = 20 * 1024 * 1024`
  - `@dataclass PickedFile(file_id, filename, size)` and `pick_file(message) -> Optional[PickedFile]`
  - `reply_for(receipt: InboxReceipt, base_url) -> str`
  - `async file_to_inbox(*, sender_name, filename, content, caption, message_ref, session_factory, base_url, receive=receive_document) -> str`
  - `build_application(token) -> Application` and `main() -> int`

Behaviour. Each point is a `UX-DEFAULT (pending Pedro)`:
- **Accepts** files (including PDFs), photos and videos. Each message becomes one Inbox item, and the caption goes to the classifier and shows in the Inbox.
- **Replies:**
  - confident: "Got it — looks like House › Warranty. Approve it in the Inbox: <link>"
  - uncertain: "Got it — I'm not sure where this belongs. Please sort it in the Inbox: <link>"
  - duplicate: "I already have this one", plus "— it's waiting in the Inbox" if it's still pending
  - over 20 MB: asks the sender to email it instead
  - unsupported file type or plain text: a one-line help message
- **Unknown users get no reply.** Their ID and name are logged at INFO for operator step O3.
- **Uploaded-by** shows as `"<Name> (Telegram)"`.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_telegram_bot.py`:

```python
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from app.channels import telegram_bot
from app.channels.telegram_bot import PickedFile, file_to_inbox, pick_file, reply_for
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.inbox_item import InboxItem
from app.services.inbox_service import InboxReceipt

pytestmark = pytest.mark.usefixtures("two_domains")


def _msg(**kw):
    base = dict(document=None, photo=None, video=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_pick_file_document():
    msg = _msg(document=SimpleNamespace(file_id="F1", file_unique_id="U1", file_name="manual.pdf",
                                        file_size=1000, mime_type="application/pdf"))
    assert pick_file(msg) == PickedFile("F1", "manual.pdf", 1000)


def test_pick_file_document_without_name_uses_mime():
    msg = _msg(document=SimpleNamespace(file_id="F1", file_unique_id="U1", file_name=None,
                                        file_size=10, mime_type="application/pdf"))
    assert pick_file(msg).filename == "telegram-file-U1.pdf"


def test_pick_file_largest_photo_and_video():
    small = SimpleNamespace(file_id="S", file_unique_id="US", file_size=10)
    large = SimpleNamespace(file_id="L", file_unique_id="UL", file_size=99)
    assert pick_file(_msg(photo=[small, large])) == PickedFile("L", "telegram-photo-UL.jpg", 99)
    video = SimpleNamespace(file_id="V", file_unique_id="UV", file_name=None, file_size=5, mime_type="video/mp4")
    assert pick_file(_msg(video=video)) == PickedFile("V", "telegram-video-UV.mp4", 5)
    assert pick_file(_msg()) is None


def _receipt(confident=True, duplicate=False, status=DocumentStatus.PENDING_REVIEW):
    doc = Document(id=1, filename="x.pdf", file_path="/x", content_hash="h",
                   source=DocumentSource.TELEGRAM, status=status)
    item = InboxItem(document_id=1, suggested_domain="house", suggested_category="manual",
                     confidence=0.95 if confident else 0.2)
    return InboxReceipt(document=doc, inbox_item=item, duplicate=duplicate)


def test_replies():
    assert "Fake › Manual" in reply_for(_receipt(), "https://hub.example")
    assert "https://hub.example/inbox" in reply_for(_receipt(), "https://hub.example")
    assert "not sure" in reply_for(_receipt(confident=False), "https://h")
    assert "waiting in the Inbox" in reply_for(_receipt(duplicate=True), "https://h")
    filed = reply_for(_receipt(duplicate=True, status=DocumentStatus.PROCESSED), "https://h")
    assert "already have" in filed and "waiting" not in filed


@pytest.mark.asyncio
async def test_file_to_inbox_passes_channel_details(engine):
    calls = []

    async def fake_receive(session, incoming, **kwargs):
        calls.append((incoming, kwargs))
        return _receipt()

    text = await file_to_inbox(
        sender_name="Rute", filename="a.pdf", content=b"%PDF", caption="boiler warranty",
        message_ref="telegram:5:6", session_factory=lambda: Session(engine),
        base_url="https://h", receive=fake_receive,
    )

    incoming, kwargs = calls[0]
    assert incoming.source == DocumentSource.TELEGRAM and incoming.uploaded_by == "Rute (Telegram)"
    assert kwargs == {"context_text": "Telegram caption: boiler warranty", "external_ref": "telegram:5:6"}
    assert "Inbox" in text


def test_main_exits_cleanly_without_token(monkeypatch, capsys):
    monkeypatch.setattr(telegram_bot.settings, "HUB_TELEGRAM_BOT_TOKEN", "")
    assert telegram_bot.main() == 0
    assert "not configured" in capsys.readouterr().out


def test_build_application_registers_handlers():
    app = telegram_bot.build_application("123456:TEST-TOKEN")
    assert sum(len(h) for h in app.handlers.values()) == 3
```

- [ ] **Step 2: Add the dependency and run.** Add `python-telegram-bot>=21.3` to `requirements.txt` after `httpx`, run `pip install -r requirements.txt`, then run `pytest tests/test_telegram_bot.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement** `app/channels/telegram_bot.py`:

```python
"""Telegram ingestion channel: the Hub's own bot (NOT the Recipes bot).
Same python-telegram-bot polling pattern as Recipes/app/bot.py, but run as
its own systemd service (deploy/systemd/home-hub-telegram.service):
    python -m app.channels.telegram_bot
Every file from an allow-listed family member goes to the Inbox via
inbox_service.receive_document. Messages from anyone else are ignored
silently (the bot is publicly findable); their id is logged so the operator
can add a family member."""

from __future__ import annotations

import logging
import mimetypes
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from sqlmodel import Session
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import settings
from app.domains.base import MediaKind
from app.domains.fields import media_kind_for
from app.models.document import DocumentSource, DocumentStatus
from app.services.inbox_service import InboxReceipt, describe, is_confident, receive_document, suggested_domain
from app.services.ingestion import IncomingFile

logger = logging.getLogger("home_hub.telegram_bot")

MAX_BOT_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Bot API getFile limit
HELP_TEXT = "Send me a PDF, a photo or a short video and I'll put it in the Hub Inbox for approval."


@dataclass
class PickedFile:
    file_id: str
    filename: str
    size: Optional[int]


def _ext(mime_type: Optional[str], default: str) -> str:
    return (mimetypes.guess_extension(mime_type) or default) if mime_type else default


def pick_file(message) -> Optional[PickedFile]:
    if message.document is not None:
        d = message.document
        return PickedFile(d.file_id, d.file_name or f"telegram-file-{d.file_unique_id}{_ext(d.mime_type, '')}", d.file_size)
    if message.photo:
        p = message.photo[-1]  # largest resolution
        return PickedFile(p.file_id, f"telegram-photo-{p.file_unique_id}.jpg", p.file_size)
    if message.video is not None:
        v = message.video
        return PickedFile(v.file_id, v.file_name or f"telegram-video-{v.file_unique_id}{_ext(v.mime_type, '.mp4')}", v.file_size)
    return None


def reply_for(receipt: InboxReceipt, base_url: str) -> str:
    inbox_url = f"{base_url.rstrip('/')}/inbox"
    if receipt.duplicate:
        if receipt.document.status == DocumentStatus.PENDING_REVIEW:
            return f"I already have this one — it's waiting in the Inbox: {inbox_url}"
        return "I already have this one — nothing to do."
    item = receipt.inbox_item
    if item is not None and is_confident(item):
        label = describe(suggested_domain(item), item.suggested_category)
        return f"Got it — looks like {label}. Approve it in the Inbox: {inbox_url}"
    return f"Got it — I'm not sure where this belongs. Please sort it in the Inbox: {inbox_url}"


async def file_to_inbox(
    *,
    sender_name: str,
    filename: str,
    content: bytes,
    caption: Optional[str],
    message_ref: str,
    session_factory: Callable[[], Session],
    base_url: str,
    receive=receive_document,
) -> str:
    with session_factory() as session:
        receipt = await receive(
            session,
            IncomingFile(filename=filename, content=content, source=DocumentSource.TELEGRAM,
                         uploaded_by=f"{sender_name} (Telegram)"),
            context_text=f"Telegram caption: {caption}" if caption else None,
            external_ref=message_ref,
        )
        return reply_for(receipt, base_url)


def _sender_name(update: Update) -> Optional[str]:
    user = update.effective_user
    name = settings.telegram_allowed_users.get(user.id) if user else None
    if name is None and user is not None:
        logger.info("ignoring message from unknown Telegram user id=%s name=%s", user.id, user.full_name)
    return name


async def on_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.db import engine

    message = update.effective_message
    sender = _sender_name(update)
    if sender is None or message is None:
        return
    picked = pick_file(message)
    if picked is None or media_kind_for(picked.filename) == MediaKind.OTHER:
        await message.reply_text(HELP_TEXT)
        return
    if picked.size and picked.size > MAX_BOT_DOWNLOAD_BYTES:
        await message.reply_text(
            "That file is over 20 MB — Telegram won't let me download it. Please email it to the Hub instead."
        )
        return
    try:
        tg_file = await context.bot.get_file(picked.file_id)
        content = bytes(await tg_file.download_as_bytearray())
        reply = await file_to_inbox(
            sender_name=sender, filename=picked.filename, content=content, caption=message.caption,
            message_ref=f"telegram:{message.chat_id}:{message.message_id}",
            session_factory=lambda: Session(engine), base_url=settings.PUBLIC_BASE_URL,
        )
    except Exception:
        logger.exception("failed to file Telegram message %s from %s", message.message_id, sender)
        reply = "Something went wrong saving that — please try again, or email it to the Hub."
    await message.reply_text(reply)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _sender_name(update) is None or update.effective_message is None:
        return
    await update.effective_message.reply_text(HELP_TEXT)


def build_application(token: str) -> Application:
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", on_text))
    application.add_handler(MessageHandler(
        (filters.Document.ALL | filters.PHOTO | filters.VIDEO) & ~filters.COMMAND, on_file
    ))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return application


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx logs every request URL at INFO -- and Bot API URLs contain the token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not settings.HUB_TELEGRAM_BOT_TOKEN:
        print("Hub Telegram bot not configured (HUB_TELEGRAM_BOT_TOKEN unset) — exiting.")
        return 0
    settings.telegram_allowed_users  # fail fast on a malformed allowlist
    build_application(settings.HUB_TELEGRAM_BOT_TOKEN).run_polling(allowed_updates=Update.ALL_TYPES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run** `pytest tests/test_telegram_bot.py -v`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add app/channels/telegram_bot.py requirements.txt tests/test_telegram_bot.py
git commit -m "feat(channels): dedicated Hub Telegram bot feeding the Inbox"
```

---

### Task 9: Git-deployed systemd user units + deploy workflow + docs

**Files:**
- Create: `deploy/systemd/home-hub-mailpoll.service`, `deploy/systemd/home-hub-mailpoll.timer`, `deploy/systemd/home-hub-telegram.service`, `deploy/install_user_units.sh`
- Modify: `.github/workflows/deploy.yml`, `docs/ARCHITECTURE.md`, `docs/SYSADMIN.md`
- Test: `tests/test_deploy_units.py`

**Interfaces:**
- Produces a convention that every future background job follows, bank sync included. Drop `<name>.service`, and optionally `<name>.timer`, into `deploy/systemd/`:
  - a `.service` **with** an `[Install]` section is long-running: it is enabled and restarted on every deploy;
  - a `.service` **without** one is a timer-driven oneshot;
  - every `.timer` is enabled.
- No sudo is involved.

- [ ] **Step 1: Write the failing test.** Create `tests/test_deploy_units.py`:

```python
import configparser
import importlib.util
import os
from pathlib import Path

UNIT_DIR = Path("deploy/systemd")


def _read(path):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(path)
    return parser


def test_expected_units_exist():
    names = {p.name for p in UNIT_DIR.iterdir()}
    assert {"home-hub-mailpoll.service", "home-hub-mailpoll.timer", "home-hub-telegram.service"} <= names


def test_services_run_app_modules_from_the_checkout_without_privilege():
    for path in UNIT_DIR.glob("*.service"):
        service = _read(path)["Service"]
        assert service["WorkingDirectory"] == "/srv/home-hub/app"
        assert service["EnvironmentFile"] == "/srv/home-hub/app/.env"
        exec_start = service["ExecStart"].split()
        assert exec_start[:2] == ["/srv/home-hub/venv/bin/python", "-m"]
        assert importlib.util.find_spec(exec_start[2]) is not None, exec_start[2]
        text = path.read_text()
        assert "User=" not in text and "ExecStartPre=+" not in text


def test_every_timer_has_a_matching_oneshot_service():
    for timer in UNIT_DIR.glob("*.timer"):
        service = _read(timer.with_suffix(".service"))
        assert service["Service"]["Type"] == "oneshot"
        assert not service.has_section("Install")


def test_install_script_is_executable():
    assert os.access("deploy/install_user_units.sh", os.X_OK)
```

- [ ] **Step 2: Run** `pytest tests/test_deploy_units.py -v`. Expected: FAIL.

- [ ] **Step 3: Create the units and the install script.**

`deploy/systemd/home-hub-mailpoll.service`:

```ini
[Unit]
Description=Home Hub - fetch new documents from the Hub mailbox into the Inbox

[Service]
Type=oneshot
WorkingDirectory=/srv/home-hub/app
EnvironmentFile=/srv/home-hub/app/.env
ExecStart=/srv/home-hub/venv/bin/python -m app.channels.email_poller
TimeoutStartSec=15min
```

`deploy/systemd/home-hub-mailpoll.timer`:

```ini
[Unit]
Description=Home Hub - poll the Hub mailbox every 10 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
```

`deploy/systemd/home-hub-telegram.service`:

```ini
[Unit]
Description=Home Hub - Telegram bot (files documents into the Inbox)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/srv/home-hub/app
EnvironmentFile=/srv/home-hub/app/.env
ExecStart=/srv/home-hub/venv/bin/python -m app.channels.telegram_bot
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

`deploy/install_user_units.sh`. After creating it, run `chmod +x deploy/install_user_units.sh`.

```bash
#!/usr/bin/env bash
# Installs/refreshes Home Hub's background jobs as systemd USER units of the
# home-hub account (no sudo). Run by .github/workflows/deploy.yml on every
# deploy. One-time prerequisite (root): loginctl enable-linger home-hub
#
# Convention for deploy/systemd/:
#   *.timer                      -> enabled + started
#   *.service with [Install]     -> long-running: enabled + restarted (picks up new code)
#   *.service without [Install]  -> timer-driven oneshot: installed only
set -euo pipefail

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
if [ ! -d "$XDG_RUNTIME_DIR" ]; then
  echo "No user systemd for $(id -un): run 'loginctl enable-linger $(id -un)' as root once." >&2
  exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)/systemd"
DEST="$HOME/.config/systemd/user"
mkdir -p "$DEST"

for unit in "$SRC"/*.service "$SRC"/*.timer; do
  install -m 0644 "$unit" "$DEST/"
done
systemctl --user daemon-reload

for timer in "$SRC"/*.timer; do
  systemctl --user enable --now "$(basename "$timer")"
done
for service in "$SRC"/*.service; do
  if grep -q '^\[Install\]' "$service"; then
    name="$(basename "$service")"
    systemctl --user enable "$name"
    systemctl --user restart "$name"
  fi
done
```

- [ ] **Step 4: Run** `pytest tests/test_deploy_units.py -v`. Expected: PASS.

- [ ] **Step 5: Extend the deploy workflow.** In `.github/workflows/deploy.yml`, append these lines to `script:`, after the existing `is-active home-hub` line:

```yaml
            bash deploy/install_user_units.sh
            sleep 3
            export XDG_RUNTIME_DIR="/run/user/$(id -u)"
            ! systemctl --user is-failed --quiet home-hub-telegram
```

A bot with no token exits 0 and shows as `inactive`, not `failed`, so the first deploy passes before Pedro's setup is done. A bot stuck in a crash loop shows as `failed`, and the Action fails.

- [ ] **Step 6: Update the docs.**

`docs/ARCHITECTURE.md`:
- **System Overview:** add the `GET/POST /inbox/*` → `app/routers/inbox.py` route and this diagram:

```
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
```
- **Processes:** the web app stays single-process. The background channels run as separate user-unit processes and share the SQLite file, using short transactions and the default busy timeout. WAL is deliberately not enabled (see the backups section of SYSADMIN).
- **Repository Layout:** add `app/channels/`, `app/models/inbox_item.py`, `app/services/{domain_classifier,inbox_service}.py`, `app/routers/inbox.py`, `app/templates/inbox/` and `deploy/`.
- **Database Schema:** `documents.status` gains `PENDING_REVIEW` and `DISCARDED`; `documents.source` gains `TELEGRAM`; add the new `inbox_items` table, noting that `suggested_domain` is a plain string validated against the registry.
- **Design Decisions:** add one paragraph on why the suggestion lives in `inbox_items`, and one on user units instead of sudo.

`docs/SYSADMIN.md`:
- **§2:** add the two user units, which live in `~home-hub/.config/systemd/user/` and are installed from `deploy/systemd/` on every deploy. Also add the Hub mailbox address and the bot's username.
- **§3:** add `bash deploy/install_user_units.sh`, the Telegram `is-failed` check, and the one-time `loginctl enable-linger home-hub` prerequisite.
- **§4:** add these commands:

```bash
# Channel status / logs (as root)
sudo -u home-hub XDG_RUNTIME_DIR=/run/user/995 systemctl --user status home-hub-telegram home-hub-mailpoll.timer
sudo -u home-hub XDG_RUNTIME_DIR=/run/user/995 systemctl --user list-units --failed
journalctl _SYSTEMD_USER_UNIT=home-hub-telegram.service --since "1 hour ago"
journalctl _SYSTEMD_USER_UNIT=home-hub-mailpoll.service --since today
# Poll the mailbox right now
sudo -u home-hub XDG_RUNTIME_DIR=/run/user/995 systemctl --user start home-hub-mailpoll.service
```
  Then explain the mailbox folders:
  - Mail that failed stays in the mailbox's `Hub-Failed` folder. To retry it, move it back to Inbox; this is safe because duplicates are skipped.
  - `Hub-Ignored` holds mail from senders who aren't on the allowlist, and mail with no usable attachment.
- **§6 secrets:**
  - `HUB_IMAP_PASSWORD` is a Gmail app password. Revoke or regenerate it in the Hub's Google account.
  - `HUB_TELEGRAM_BOT_TOKEN` is regenerated via @BotFather's `/revoke` command.
  - The allowlists `HUB_IMAP_ALLOWED_SENDERS` and `HUB_TELEGRAM_ALLOWED_USERS` are not secret.
  - All of these live only in `/srv/home-hub/app/.env`.
- **§7 costs:** one Haiku call per channel attachment; the allowlists cap the exposure.

- [ ] **Step 7: Run** `pytest -q`. Expected: all pass.

- [ ] **Step 8: Commit.**

```bash
git add deploy .github/workflows/deploy.yml docs/ARCHITECTURE.md docs/SYSADMIN.md tests/test_deploy_units.py
git commit -m "feat(deploy): git-deployed systemd user units for mail poller and Telegram bot"
```

---

### Task 10: Go-live (production), operator runbook

This task has no code.
- Every SSH step needs Pedro's explicit go-ahead in the session.
- The push needs his explicit order (`Personal/CLAUDE.md`).
- Push from the `Personal/` root with `git subtree push --prefix="Home & Family" home_hub main`. Never push to `origin`.

- [ ] **Step 1:** Do operator step **O1**, then confirm that `/run/user/995` exists.
- [ ] **Step 2:** Deploy with the channels switched off.
  1. Back up the DB on the VPS: `cp data/home_family.db /root/home_family_pre_inbox_$(date +%Y%m%d).db`.
  2. Push.
  3. Watch the Action: the migration applied, `home-hub` is active, and the Telegram unit is not failed.
  4. Check that `https://hub.cdafamily.casa/inbox` shows "Nothing waiting", that `home-hub-mailpoll.timer` is listed, and that the mail-poll journal says "not configured".
- [ ] **Step 3:** Pedro does **M1, M2 and M4**. The operator then does **O2**, adding the IMAP settings, the token and the sender allowlist, but leaving the Telegram allowlist empty. The bot service should show `active (running)`.
- [ ] **Step 4:** The family does **M3**. First confirm that four `ignoring message from unknown Telegram user` lines appeared, then the operator does **O3**.
- [ ] **Step 5: Smoke test (O4), with Pedro.**
  1. Pedro sends a PDF manual to the bot, with a caption.
     - The bot replies with its suggestion and the Inbox link.
     - The card appears in `/inbox`, from "Pedro (Telegram)".
  2. Pedro emails a bill PDF from an address on the allowlist.
     - Within 10 minutes (or after a manual poll), it appears in `/inbox`.
     - The mail is in `Hub-Processed`.
  3. Approve the House item, filling in Item name and Room on the card.
     - The result line links to the House document page.
     - The wiki log shows `REVIEW "Approved …"` followed by the INGEST entry.
     - The item's wiki page exists.
  4. Discard the other item.
     - The card reads "Discarded (kept on file)".
     - Nothing new appears in the wiki apart from the `REVIEW "Discarded …"` log line.
  5. Send the same PDF again. The bot replies "I already have this one".
  6. Send an email from an address that isn't on the allowlist. It lands in `Hub-Ignored` and never appears in `/inbox`.
- [ ] **Step 6:** At the end of the session, use session-handoff to record the bot username, the mailbox address (never the password) and the unit names.

---

## Self-review notes

- **Spec coverage:**
  - Dedicated mailbox with an IMAP timer (T7, T9).
  - A single address, with the LLM doing the routing (T3, T7).
  - Domain and category limited to the domains in the registry (T3).
  - Every channel document is reviewed: confident ones come pre-filled for approval, uncertain ones for editing (T4, T5).
  - A shared, top-level Inbox (T5).
  - A dedicated Telegram bot, using polling and python-telegram-bot, with a 4-person allowlist and `uploaded_by` set to the sender (T8).
  - Manual fields (item_name, room, service date) captured at approval through Plan A's `build_form_fields` and `_field_inputs.html` (T5).
  - Pending documents never touch the wiki; approval triggers Plan A's finalize and Ingest; approve and discard write REVIEW log entries (T4).
  - Status naming (T1).
  - Pedro's manual steps (the setup section above).
  - Secrets handling (T6, T9).
- **Editing after filing** is Plan A's generic re-filing, per Pedro's decision. The Inbox covers first-time filing only and builds no editor. The result line links to the document page (or the record page, for RECORD categories), where Plan A's edit and re-file screens live.
- **RECORD categories** (Plan A Tasks 20–22) need no Inbox-specific code. The picker lists them from the registry, `build_form_fields` renders their fields, and `finalize_document` creates the Record with the file attached. This is tested in T4 and T5.
- **Removed during reconciliation:**
  - The assumed `store_document`/`dispatch_document`, the `MetadataForm` protocol and a `knowledge_log` module. Plan A's real contracts replace them.
  - The Bills-list domain filter, because Plan A's Task 5 already provides it.
  - `SETTLED_DOCUMENT_STATUSES` is not defined. The invariant is "`domain IS NOT NULL` means finalized".
