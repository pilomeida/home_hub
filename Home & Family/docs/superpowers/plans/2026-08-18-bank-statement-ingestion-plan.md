# Bank Statement & Multi-Transaction Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the PDF-to-image extraction mechanism with native Claude PDF document blocks, classify uploads as bills or bank statements, and extract every transaction line item from a statement into its own `Transaction` row — while leaving the existing single-transaction bill flow behaviorally unchanged.

**Architecture:** A shared `build_content_block` helper turns any uploaded file into a Claude `document` (PDF, all pages) or `image` content block. `ingest_document` now classifies the upload first, then branches: the **bill path** is the existing sub-project-1 flow (extract → categorize → dedup → todo → wiki), unchanged in behavior; the **statement path** extracts every line item in one call and creates one `Transaction` per item, explicitly skipping dedup/todo/wiki (historical, already-settled data, not upcoming bills or standing facts).

**Tech Stack:** Same as sub-project 1 (FastAPI, SQLModel, Alembic, Jinja2, Anthropic SDK), minus `pdf2image`/`pdfplumber` (no longer needed — native PDF support replaces them). Claude model IDs: `claude-sonnet-5` for both bill and statement extraction, `claude-haiku-4-5-20251001` for bill-vs-statement classification (matching the existing wiki-assessment model choice).

**Spec:** [`docs/superpowers/specs/2026-08-18-bank-statement-ingestion-design.md`](../specs/2026-08-18-bank-statement-ingestion-design.md)

## Global Constraints

- No `pdf2image`/`poppler` dependency anywhere in the codebase after this plan — replaced by native `document` content blocks.
- Every Claude call in this plan uses `thinking={"type": "disabled"}` and passes its response through `strip_json_fences` (from `app/services/json_utils.py`) before `json.loads` — this is a real production lesson from sub-project 1 (Claude Sonnet 5 can wrap JSON in a markdown fence).
- `ingest_document` remains the single entry point every ingestion channel calls, and it must never propagate an uncaught exception — any failure (classification, extraction, or transaction-creation) lands the Document on `needs_attention` with a reason, never crashes the request and never leaves the Document stuck at `PENDING`. This is the same "ingestion boundary" principle established in sub-project 1's Task 12.
- Bill-derived `Transaction` rows always have `transaction_type=TransactionType.DEBIT` — the bill path's behavior and output are otherwise unchanged from sub-project 1.
- Statement-derived transactions never trigger `find_duplicate_transaction`, `generate_todo_for_transaction`, or `assess_and_update_wiki` — those only apply to the bill path.
- `_spend_by_category` on the dashboard only sums `transaction_type == DEBIT` transactions.

## File Structure

```
Home & Family/
├── app/
│   ├── models/
│   │   └── transaction.py             # MODIFIED: + TransactionType enum, + 6 Category values, + Transaction.transaction_type
│   ├── services/
│   │   ├── document_input.py          # NEW: build_content_block (PDF/image → Claude content block)
│   │   ├── extraction.py              # MODIFIED: extract_bill uses build_content_block; + classify_document, + extract_statement_transactions
│   │   ├── categorization.py          # MODIFIED: + synonyms for the 6 new categories
│   │   ├── pipeline.py                # MODIFIED: classify-then-branch (bill path / statement path)
│   │   └── dashboard_service.py       # MODIFIED: _spend_by_category filters to DEBIT
│   ├── routers/
│   │   └── bills.py                   # MODIFIED: bill_detail fetches all transactions, not one
│   └── templates/bills/
│       └── detail.html                # MODIFIED: renders a transaction table instead of one optional row
├── alembic/
│   ├── env.py                         # MODIFIED: render_as_batch=True (future-proofing for SQLite ALTERs)
│   └── versions/
│       └── <new>_add_transaction_type_and_expand_category.py   # NEW: hand-written batch migration
├── requirements.txt                   # MODIFIED: remove pdf2image, pdfplumber
└── tests/
    ├── test_document_input.py         # NEW
    ├── test_extraction.py             # MODIFIED: + PDF document-block test, + classify_document tests, + extract_statement_transactions tests
    ├── test_categorization.py         # MODIFIED: + new category synonym cases
    ├── test_transaction_model.py      # MODIFIED: + transaction_type / new-category round-trip test
    ├── test_pipeline.py               # REWRITTEN: classify_document replaces ensure_image throughout; + statement-path tests
    ├── test_dashboard_service.py      # MODIFIED: + DEBIT-only spend test
    ├── test_bills_router.py           # MODIFIED: + multi-transaction detail test
    └── test_e2e_bill_flow.py          # MODIFIED: classify_document replaces ensure_image; + statement e2e test
```

---

### Task 1: Shared document content-block builder

**Files:**
- Create: `app/services/document_input.py`
- Test: `tests/test_document_input.py`

**Interfaces:**
- Produces: `app.services.document_input.build_content_block(file_path: str) -> dict` (a Claude `document` or `image` content block); `app.services.document_input.UnsupportedFileTypeError(Exception)`

- [ ] **Step 1: Write the failing tests `tests/test_document_input.py`**

```python
import base64

import pytest

from app.services.document_input import UnsupportedFileTypeError, build_content_block


def test_build_content_block_for_pdf(tmp_path):
    pdf_path = tmp_path / "bill.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake pdf bytes")

    block = build_content_block(str(pdf_path))

    assert block["type"] == "document"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "application/pdf"
    assert block["source"]["data"] == base64.b64encode(b"%PDF-1.4 fake pdf bytes").decode()


def test_build_content_block_for_png(tmp_path):
    png_path = tmp_path / "photo.png"
    png_path.write_bytes(b"fake-png-bytes")

    block = build_content_block(str(png_path))

    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/png"


def test_build_content_block_for_jpeg(tmp_path):
    for suffix in (".jpg", ".jpeg"):
        jpeg_path = tmp_path / f"photo{suffix}"
        jpeg_path.write_bytes(b"fake-jpeg-bytes")

        block = build_content_block(str(jpeg_path))

        assert block["type"] == "image"
        assert block["source"]["media_type"] == "image/jpeg"


def test_build_content_block_raises_on_unsupported_extension(tmp_path):
    txt_path = tmp_path / "notes.txt"
    txt_path.write_bytes(b"plain text")

    with pytest.raises(UnsupportedFileTypeError):
        build_content_block(str(txt_path))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_document_input.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.document_input'`

- [ ] **Step 3: Write `app/services/document_input.py`**

```python
"""Builds Claude content blocks (document or image) for an uploaded file."""

import base64
from pathlib import Path

_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class UnsupportedFileTypeError(Exception):
    pass


def build_content_block(file_path: str) -> dict:
    """Return a Claude content block for the given file: a `document` block
    for PDFs (the whole file, all pages) or an `image` block for image
    files."""
    path = Path(file_path)
    suffix = path.suffix.lower()
    data = base64.b64encode(path.read_bytes()).decode()

    if suffix == ".pdf":
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": data},
        }
    if suffix in _IMAGE_MEDIA_TYPES:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": _IMAGE_MEDIA_TYPES[suffix], "data": data},
        }
    raise UnsupportedFileTypeError(f"Unsupported file type: {suffix}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_document_input.py -v`
Expected: PASS (4 tests, one parametrized-by-hand covering 2 cases = 5 assertions)

- [ ] **Step 5: Commit**

```bash
git add app/services/document_input.py tests/test_document_input.py
git commit -m "feat: add shared PDF/image content-block builder for Claude document input"
```

---

### Task 2: Refactor `extract_bill` onto native PDF document blocks

**Files:**
- Modify: `app/services/extraction.py`
- Modify: `tests/test_extraction.py`

**Interfaces:**
- Consumes: `app.services.document_input.build_content_block` (Task 1)
- Produces: `app.services.extraction.extract_bill(file_path: str, client=None) -> ExtractedBill` (same dataclass and error type as before; parameter renamed from `image_path` to `file_path` but still positional-compatible with every existing call site). `ensure_image` and its `pdf2image` import are removed entirely.

- [ ] **Step 1: Rewrite `app/services/extraction.py`**

```python
"""Claude-based structured extraction from bill/statement documents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Optional

from anthropic import AsyncAnthropic

from app.config import settings
from app.services.document_input import build_content_block
from app.services.json_utils import strip_json_fences

_MODEL = "claude-sonnet-5"

_SYSTEM_PROMPT = """You extract structured billing data from a bill or bank \
statement document. Respond with ONLY a JSON object, no prose, matching this \
shape exactly:

{
  "provider": "string, the company/entity that issued the bill",
  "category_hint": "one lowercase word: electricity, water, gas, telecom, \
insurance, subscriptions, groceries, health, home, or other",
  "amount": 0.00,
  "currency": "3-letter ISO code, default EUR",
  "due_date": "YYYY-MM-DD or null",
  "paid_date": "YYYY-MM-DD or null",
  "statement_period": "YYYY-MM or null"
}

If a field cannot be determined, use null (or 0.0 for amount as a last resort)."""


class ExtractionError(Exception):
    pass


@dataclass
class ExtractedBill:
    provider: str
    category_hint: str
    amount: float
    currency: str
    due_date: Optional[date]
    paid_date: Optional[date]
    statement_period: Optional[str]


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return date.fromisoformat(value)


async def extract_bill(file_path: str, client: Optional[AsyncAnthropic] = None) -> ExtractedBill:
    """Extract structured billing data from a bill/statement document (the
    whole PDF, all pages, or a single image)."""
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    content_block = build_content_block(file_path)

    message = await anthropic_client.messages.create(
        model=_MODEL,
        max_tokens=4096,
        thinking={"type": "disabled"},
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": "Extract the billing data as JSON."},
                ],
            }
        ],
    )

    try:
        raw_text = strip_json_fences(message.content[0].text)
        data = json.loads(raw_text)
        return ExtractedBill(
            provider=data["provider"],
            category_hint=data.get("category_hint", "other"),
            amount=float(data.get("amount") or 0.0),
            currency=data.get("currency") or "EUR",
            due_date=_parse_date(data.get("due_date")),
            paid_date=_parse_date(data.get("paid_date")),
            statement_period=data.get("statement_period"),
        )
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ExtractionError(f"Could not parse extraction response: {exc}") from exc
```

- [ ] **Step 2: Run the existing extraction tests to confirm they still pass unchanged**

Run: `pytest tests/test_extraction.py -v`
Expected: PASS — all 4 existing tests use `.png` fixture files, which `build_content_block` still routes to an `image` block exactly as `extract_bill` did before. Nothing about their assertions changes.

- [ ] **Step 3: Add a new failing test proving PDFs now go through the whole-file `document` block, not a rasterized page**

Append to `tests/test_extraction.py`:

```python
@pytest.mark.asyncio
async def test_extract_bill_sends_whole_pdf_as_document_block(tmp_path):
    pdf_path = tmp_path / "bill.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake pdf bytes")
    response = json.dumps({
        "provider": "EDP", "category_hint": "electricity", "amount": 42.0,
        "currency": "EUR", "due_date": None, "paid_date": None,
        "statement_period": "2026-08",
    })
    client = _FakeAnthropicClient(response)
    captured = {}
    original_create = client.messages.create

    async def capturing_create(**kwargs):
        captured["messages"] = kwargs["messages"]
        return await original_create(**kwargs)

    client.messages.create = capturing_create

    result = await extract_bill(str(pdf_path), client=client)

    assert result.provider == "EDP"
    content_block = captured["messages"][0]["content"][0]
    assert content_block["type"] == "document"
    assert content_block["source"]["media_type"] == "application/pdf"
```

- [ ] **Step 4: Run the new test to verify it fails first, then passes**

Run: `pytest tests/test_extraction.py::test_extract_bill_sends_whole_pdf_as_document_block -v`
Expected: with Step 1 already applied, this should PASS immediately (there's no separate RED step here since the implementation and the proving test land together — that's fine, the RED/GREEN cycle for this refactor was Steps 1→2 above, confirming no regression; this test adds new coverage for the new behavior). Run the full file once more to confirm nothing broke: `pytest tests/test_extraction.py -v` → PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/extraction.py tests/test_extraction.py
git commit -m "refactor: extract_bill uses native PDF document blocks, drop ensure_image"
```

---

### Task 3: Document classification (bill vs. statement)

**Files:**
- Modify: `app/services/extraction.py`
- Modify: `tests/test_extraction.py`

**Interfaces:**
- Consumes: `app.services.document_input.build_content_block` (Task 1), `app.services.json_utils.strip_json_fences`
- Produces: `app.services.extraction.classify_document(file_path: str, client=None) -> str` (returns `"bill"` or `"statement"`, async); `app.services.extraction.ClassificationError(Exception)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_extraction.py`:

```python
@pytest.mark.asyncio
async def test_classify_document_returns_bill(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient(json.dumps({"document_type": "bill"}))

    result = await classify_document(str(pdf_path), client=client)

    assert result == "bill"


@pytest.mark.asyncio
async def test_classify_document_returns_statement(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient(json.dumps({"document_type": "statement"}))

    result = await classify_document(str(pdf_path), client=client)

    assert result == "statement"


@pytest.mark.asyncio
async def test_classify_document_raises_on_malformed_response(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient("not json")

    with pytest.raises(ClassificationError):
        await classify_document(str(pdf_path), client=client)


@pytest.mark.asyncio
async def test_classify_document_raises_on_unexpected_type(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient(json.dumps({"document_type": "receipt"}))

    with pytest.raises(ClassificationError):
        await classify_document(str(pdf_path), client=client)
```

Update the import line at the top of `tests/test_extraction.py` to also bring in the new names:

```python
from app.services.extraction import (
    ClassificationError,
    ExtractedBill,
    ExtractionError,
    classify_document,
    extract_bill,
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_extraction.py -v`
Expected: FAIL — `ImportError: cannot import name 'ClassificationError'` (and `classify_document`)

- [ ] **Step 3: Add to `app/services/extraction.py`** (after the existing `extract_bill` function)

```python
_CLASSIFICATION_MODEL = "claude-haiku-4-5-20251001"

_CLASSIFICATION_SYSTEM_PROMPT = """You classify an uploaded financial \
document as either a single bill/invoice or a bank account statement. \
Respond with ONLY a JSON object:

{"document_type": "bill" or "statement"}

A "bill" has one provider and one amount due (an invoice, receipt, or \
premium notice). A "statement" lists multiple transactions across one or \
more accounts (a monthly bank/account statement)."""


class ClassificationError(Exception):
    pass


async def classify_document(file_path: str, client: Optional[AsyncAnthropic] = None) -> str:
    """Classify an uploaded document as "bill" or "statement"."""
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    content_block = build_content_block(file_path)

    message = await anthropic_client.messages.create(
        model=_CLASSIFICATION_MODEL,
        max_tokens=128,
        thinking={"type": "disabled"},
        system=_CLASSIFICATION_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": "Classify this document as JSON."},
                ],
            }
        ],
    )

    try:
        raw_text = strip_json_fences(message.content[0].text)
        data = json.loads(raw_text)
        document_type = data["document_type"]
        if document_type not in ("bill", "statement"):
            raise ValueError(f"Unexpected document_type: {document_type!r}")
        return document_type
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ClassificationError(f"Could not classify document: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_extraction.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/extraction.py tests/test_extraction.py
git commit -m "feat: add bill-vs-statement document classification"
```

---

### Task 4: `TransactionType`, expanded `Category`, and the batch migration

**Files:**
- Modify: `app/models/transaction.py`
- Modify: `app/services/categorization.py`
- Modify: `alembic/env.py`
- Create: `alembic/versions/<generated>_add_transaction_type_and_expand_category.py`
- Modify: `tests/test_transaction_model.py`
- Modify: `tests/test_categorization.py`

**Interfaces:**
- Produces: `app.models.transaction.TransactionType` enum (`DEBIT` / `CREDIT` / `TRANSFER`); `app.models.transaction.Category` gains `INCOME`, `TRANSFER`, `ATM_WITHDRAWAL`, `RESTAURANTS`, `SHOPPING`, `OTHER_EXPENSE`; `app.models.transaction.Transaction.transaction_type: TransactionType` (default `DEBIT`).

- [ ] **Step 1: Write the failing test in `tests/test_transaction_model.py`**

Append:

```python
from app.models.transaction import TransactionType


def test_transaction_type_defaults_to_debit(session):
    document = Document(
        filename="edp-august.pdf", file_path="/tmp/edp2.pdf",
        content_hash="hash2", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=87.32, currency="EUR", statement_period="2026-08",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    assert transaction.transaction_type == TransactionType.DEBIT


def test_transaction_supports_new_category_and_credit_type(session):
    document = Document(
        filename="statement.pdf", file_path="/tmp/statement.pdf",
        content_hash="hash3", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="SALARIO EMPRESA X", category=Category.INCOME,
        transaction_type=TransactionType.CREDIT, amount=2200.0, currency="EUR",
        statement_period="2026-07",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    assert transaction.category == Category.INCOME
    assert transaction.transaction_type == TransactionType.CREDIT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_transaction_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'TransactionType'`

- [ ] **Step 3: Update `app/models/transaction.py`**

```python
"""Transaction: an extracted line item from a Document, plus the controlled
category vocabulary."""

from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class Category(str, Enum):
    ELECTRICITY = "electricity"
    WATER = "water"
    GAS = "gas"
    TELECOM = "telecom"
    INSURANCE = "insurance"
    SUBSCRIPTIONS = "subscriptions"
    GROCERIES = "groceries"
    HEALTH = "health"
    HOME = "home"
    INCOME = "income"
    TRANSFER = "transfer"
    ATM_WITHDRAWAL = "atm_withdrawal"
    RESTAURANTS = "restaurants"
    SHOPPING = "shopping"
    OTHER_EXPENSE = "other_expense"
    OTHER = "other"


class TransactionType(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"
    TRANSFER = "transfer"


class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", index=True)
    provider: str
    category: Category = Field(default=Category.OTHER)
    transaction_type: TransactionType = Field(default=TransactionType.DEBIT)
    amount: float
    currency: str = Field(default="EUR")
    due_date: Optional[date] = None
    paid_date: Optional[date] = None
    statement_period: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_transaction_model.py -v`
Expected: PASS (3 tests) — this uses the test suite's `SQLModel.metadata.create_all` DB setup, which reflects the model directly; it does not exercise the Alembic migration (that's verified separately in Step 8 below).

- [ ] **Step 5: Add category synonyms — write the failing test in `tests/test_categorization.py`**

Append:

```python
@pytest.mark.parametrize("hint,expected", [
    ("income", Category.INCOME),
    ("salary", Category.INCOME),
    ("transfer", Category.TRANSFER),
    ("atm_withdrawal", Category.ATM_WITHDRAWAL),
    ("atm", Category.ATM_WITHDRAWAL),
    ("withdrawal", Category.ATM_WITHDRAWAL),
    ("restaurants", Category.RESTAURANTS),
    ("restaurant", Category.RESTAURANTS),
    ("dining", Category.RESTAURANTS),
    ("shopping", Category.SHOPPING),
    ("retail", Category.SHOPPING),
    ("other_expense", Category.OTHER_EXPENSE),
])
def test_normalize_category_new_statement_categories(hint, expected):
    assert normalize_category(hint) == expected
```

- [ ] **Step 6: Run test to verify it fails, then update `app/services/categorization.py`**

Run: `pytest tests/test_categorization.py -v` — expect the new cases to FAIL (they all resolve to `Category.OTHER` today).

Update the `_SYNONYMS` dict in `app/services/categorization.py` by adding these entries (keep every existing entry unchanged):

```python
    "income": Category.INCOME,
    "salary": Category.INCOME,
    "transfer": Category.TRANSFER,
    "atm_withdrawal": Category.ATM_WITHDRAWAL,
    "atm": Category.ATM_WITHDRAWAL,
    "withdrawal": Category.ATM_WITHDRAWAL,
    "restaurants": Category.RESTAURANTS,
    "restaurant": Category.RESTAURANTS,
    "dining": Category.RESTAURANTS,
    "shopping": Category.SHOPPING,
    "retail": Category.SHOPPING,
    "other_expense": Category.OTHER_EXPENSE,
```

Run: `pytest tests/test_categorization.py -v`
Expected: PASS (22 parametrized cases total)

- [ ] **Step 7: Enable batch mode in `alembic/env.py`** (future-proofing: SQLite can't ALTER most column properties directly, and this migration needs `op.batch_alter_table`)

In both `run_migrations_offline` and `run_migrations_online`, add `render_as_batch=True` to the `context.configure(...)` call:

```python
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import engine_from_config, pool

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
```

- [ ] **Step 8: Write the migration by hand (not autogenerate)**

Alembic's autogenerate diff does not reliably detect a widened `sa.Enum` value set as a change on SQLite (the column often compares as unchanged), so **do not** rely on `alembic revision --autogenerate` for this one. Instead:

```bash
alembic revision -m "add transaction_type and expand category"
```

This creates an empty migration file under `alembic/versions/`. Find its generated revision id (in the file's `revision = "..."` line) and confirm `down_revision = "ebf9c2a43192"` (the current head — verify with `alembic heads` if the chain has moved). Replace the file's `upgrade`/`downgrade` bodies with:

```python
def upgrade() -> None:
    with op.batch_alter_table("transactions", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "transaction_type",
                sa.Enum("DEBIT", "CREDIT", "TRANSFER", name="transactiontype"),
                nullable=False,
                server_default="DEBIT",
            )
        )
        batch_op.alter_column(
            "category",
            existing_type=sa.Enum(
                "ELECTRICITY", "WATER", "GAS", "TELECOM", "INSURANCE",
                "SUBSCRIPTIONS", "GROCERIES", "HEALTH", "HOME", "OTHER",
                name="category",
            ),
            type_=sa.Enum(
                "ELECTRICITY", "WATER", "GAS", "TELECOM", "INSURANCE",
                "SUBSCRIPTIONS", "GROCERIES", "HEALTH", "HOME", "INCOME",
                "TRANSFER", "ATM_WITHDRAWAL", "RESTAURANTS", "SHOPPING",
                "OTHER_EXPENSE", "OTHER",
                name="category",
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("transactions", recreate="always") as batch_op:
        batch_op.drop_column("transaction_type")
        batch_op.alter_column(
            "category",
            existing_type=sa.Enum(
                "ELECTRICITY", "WATER", "GAS", "TELECOM", "INSURANCE",
                "SUBSCRIPTIONS", "GROCERIES", "HEALTH", "HOME", "INCOME",
                "TRANSFER", "ATM_WITHDRAWAL", "RESTAURANTS", "SHOPPING",
                "OTHER_EXPENSE", "OTHER",
                name="category",
            ),
            type_=sa.Enum(
                "ELECTRICITY", "WATER", "GAS", "TELECOM", "INSURANCE",
                "SUBSCRIPTIONS", "GROCERIES", "HEALTH", "HOME", "OTHER",
                name="category",
            ),
        )
```

Add `import sqlalchemy as sa` and `from alembic import op` at the top of the file if the generated stub didn't already include them (check the existing migrations under `alembic/versions/` for the exact header style to match — `revision`/`down_revision`/`branch_labels`/`depends_on` typed the same way as `a31842449acb_add_transactions_table.py`).

`recreate="always"` forces batch mode to fully rebuild the table (SQLite has no `ALTER ... DROP CONSTRAINT` for a `CHECK` constraint, which is how `sa.Enum` is implemented there) rather than relying on batch mode's `"auto"` heuristic to correctly detect that a constraint-changing rebuild is needed — for an enum value-set change, that heuristic is not reliable, so be explicit.

- [ ] **Step 9: Apply and verify the migration against a scratch database**

```bash
cp data/home_family.db /tmp/home_family_pre_migration.db 2>/dev/null || true  # harmless if it doesn't exist yet in this worktree
alembic upgrade head
sqlite3 data/home_family.db ".schema transactions"
```

Confirm the printed schema shows both the new `transaction_type` column and a `category` CHECK constraint listing all 16 values (10 original + 6 new). Then verify the downgrade path is sound (round-trip, don't leave the DB downgraded):

```bash
alembic downgrade -1
sqlite3 data/home_family.db ".schema transactions"   # should show the 10-value category constraint, no transaction_type column
alembic upgrade head
sqlite3 data/home_family.db ".schema transactions"   # back to 16-value constraint + transaction_type column
```

- [ ] **Step 10: Commit**

```bash
git add app/models/transaction.py app/services/categorization.py alembic/env.py alembic/versions/ tests/test_transaction_model.py tests/test_categorization.py
git commit -m "feat: add TransactionType and expand Category for bank-statement line items"
```

---

### Task 5: Statement transaction extraction

**Files:**
- Modify: `app/services/extraction.py`
- Modify: `tests/test_extraction.py`

**Interfaces:**
- Consumes: `app.services.document_input.build_content_block` (Task 1)
- Produces: `app.services.extraction.ExtractedTransaction` dataclass (`transaction_date: date`, `description: str`, `amount: float`, `currency: str`, `transaction_type: str`, `category_hint: str`); `app.services.extraction.ExtractedStatement` dataclass (`statement_period: Optional[str]`, `transactions: list[ExtractedTransaction]`); `app.services.extraction.StatementExtractionError(Exception)`; `app.services.extraction.extract_statement_transactions(file_path: str, client=None) -> ExtractedStatement` (async)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_extraction.py`:

```python
@pytest.mark.asyncio
async def test_extract_statement_transactions_parses_valid_response(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    response = json.dumps({
        "statement_period": "2026-07",
        "transactions": [
            {"date": "2026-07-05", "description": "CONTINENTE MAFRA", "amount": 42.15,
             "currency": "EUR", "type": "debit", "category_hint": "groceries"},
            {"date": "2026-07-10", "description": "SALARIO EMPRESA X", "amount": 2200.00,
             "currency": "EUR", "type": "credit", "category_hint": "income"},
            {"date": "2026-07-12", "description": "TRANSFERENCIA PARA REVOLUT", "amount": 100.00,
             "currency": "EUR", "type": "transfer", "category_hint": "transfer"},
        ],
    })
    client = _FakeAnthropicClient(response)

    result = await extract_statement_transactions(str(pdf_path), client=client)

    assert isinstance(result, ExtractedStatement)
    assert result.statement_period == "2026-07"
    assert len(result.transactions) == 3
    assert result.transactions[0].description == "CONTINENTE MAFRA"
    assert result.transactions[0].transaction_date.isoformat() == "2026-07-05"
    assert result.transactions[0].transaction_type == "debit"
    assert result.transactions[1].transaction_type == "credit"
    assert result.transactions[2].transaction_type == "transfer"


@pytest.mark.asyncio
async def test_extract_statement_transactions_raises_on_malformed_json(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient("not json")

    with pytest.raises(StatementExtractionError):
        await extract_statement_transactions(str(pdf_path), client=client)


@pytest.mark.asyncio
async def test_extract_statement_transactions_raises_when_transactions_key_missing(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient(json.dumps({"statement_period": "2026-07"}))

    with pytest.raises(StatementExtractionError):
        await extract_statement_transactions(str(pdf_path), client=client)


@pytest.mark.asyncio
async def test_extract_statement_transactions_handles_empty_list(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient(json.dumps({"statement_period": "2026-07", "transactions": []}))

    result = await extract_statement_transactions(str(pdf_path), client=client)

    assert result.transactions == []
```

Update the import line at the top of `tests/test_extraction.py` once more:

```python
from app.services.extraction import (
    ClassificationError,
    ExtractedBill,
    ExtractedStatement,
    ExtractionError,
    StatementExtractionError,
    classify_document,
    extract_bill,
    extract_statement_transactions,
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_extraction.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Add to `app/services/extraction.py`** (after `classify_document`)

```python
_STATEMENT_MODEL = "claude-sonnet-5"

_STATEMENT_SYSTEM_PROMPT = """You extract every transaction line item from a \
bank account statement document (all pages). Respond with ONLY a JSON \
object, no prose, matching this shape exactly:

{
  "statement_period": "YYYY-MM",
  "transactions": [
    {
      "date": "YYYY-MM-DD",
      "description": "string, the merchant or counterparty",
      "amount": 0.00,
      "currency": "3-letter ISO code, default EUR",
      "type": "debit, credit, or transfer — use transfer for a move \
between the account holder's own accounts. This includes indirect \
transfers: a Santander statement may show this as a payment to a \
temporary/virtual MB WAY-issued card (used to top up a Revolut account) \
rather than a literal 'transfer to Revolut' line — treat an MB WAY \
temporary card top-up as a transfer, not an ordinary purchase. On a \
Revolut statement, the matching incoming top-up (from Santander, \
directly or via such a card) is also a transfer, not income. A direct \
transfer in the other direction (Revolut back to Santander) is a \
transfer too.",
      "category_hint": "one lowercase word: electricity, water, gas, \
telecom, insurance, subscriptions, groceries, health, home, income, \
transfer, atm_withdrawal, restaurants, shopping, or other_expense"
    }
  ]
}

Include every transaction line item found across all pages of the statement."""


class StatementExtractionError(Exception):
    pass


@dataclass
class ExtractedTransaction:
    transaction_date: date
    description: str
    amount: float
    currency: str
    transaction_type: str
    category_hint: str


@dataclass
class ExtractedStatement:
    statement_period: Optional[str]
    transactions: list[ExtractedTransaction]


async def extract_statement_transactions(
    file_path: str, client: Optional[AsyncAnthropic] = None
) -> ExtractedStatement:
    """Extract every transaction line item from a bank statement document."""
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    content_block = build_content_block(file_path)

    message = await anthropic_client.messages.create(
        model=_STATEMENT_MODEL,
        max_tokens=8192,
        thinking={"type": "disabled"},
        system=_STATEMENT_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": "Extract every transaction as JSON."},
                ],
            }
        ],
    )

    try:
        raw_text = strip_json_fences(message.content[0].text)
        data = json.loads(raw_text)
        transactions = [
            ExtractedTransaction(
                transaction_date=date.fromisoformat(item["date"]),
                description=item["description"],
                amount=float(item["amount"]),
                currency=item.get("currency") or "EUR",
                transaction_type=item["type"],
                category_hint=item.get("category_hint", "other_expense"),
            )
            for item in data["transactions"]
        ]
        return ExtractedStatement(statement_period=data.get("statement_period"), transactions=transactions)
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise StatementExtractionError(f"Could not parse statement extraction response: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_extraction.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/extraction.py tests/test_extraction.py
git commit -m "feat: add multi-transaction bank statement extraction"
```

---

### Task 6: Pipeline branching — classify, then bill path or statement path

**Files:**
- Modify: `app/services/pipeline.py`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `classify_document`, `extract_bill`, `extract_statement_transactions`, `ExtractedBill` (Tasks 2, 3, 5); `TransactionType` (Task 4); `normalize_category`, `find_duplicate_transaction`, `generate_todo_for_transaction`, `assess_and_update_wiki` (unchanged from sub-project 1)
- Produces: `app.services.pipeline.ingest_document(session, document) -> Document` (async) — same signature as before; internally now classifies first and branches to `_ingest_bill` or `_ingest_statement` (both private, not part of the public interface other modules should call)

- [ ] **Step 1: Rewrite `tests/test_pipeline.py` in full**

```python
from datetime import date

import pytest
from sqlmodel import select

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Category, Transaction, TransactionType
from app.models.todo import Todo
from app.models.wiki import WikiPage
from app.services import pipeline
from app.services.extraction import ExtractedBill, ExtractedStatement, ExtractedTransaction, ExtractionError


def _make_document(session, tmp_path, filename="bill.pdf", content_hash="hash1"):
    document = Document(
        filename=filename, file_path=str(tmp_path / filename), content_hash=content_hash,
        source=DocumentSource.MANUAL, status=DocumentStatus.PENDING,
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


async def _fake_classify_bill(file_path, client=None):
    return "bill"


async def _fake_classify_statement(file_path, client=None):
    return "statement"


@pytest.mark.asyncio
async def test_ingest_document_creates_transaction(session, monkeypatch, tmp_path):
    document = _make_document(session, tmp_path)

    extracted = ExtractedBill(
        provider="EDP", category_hint="electricity", amount=87.32, currency="EUR",
        due_date=date(2026, 9, 5), paid_date=None, statement_period="2026-08",
    )

    async def fake_extract_bill(file_path, client=None):
        return extracted

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return None

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline, "assess_and_update_wiki", fake_assess_and_update_wiki)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.PROCESSED
    transaction = session.exec(
        select(Transaction).where(Transaction.document_id == document.id)
    ).first()
    assert transaction is not None
    assert transaction.provider == "EDP"
    assert transaction.category == Category.ELECTRICITY
    assert transaction.amount == 87.32
    assert transaction.transaction_type == TransactionType.DEBIT


@pytest.mark.asyncio
async def test_ingest_document_marks_needs_attention_on_classification_failure(session, monkeypatch, tmp_path):
    document = _make_document(session, tmp_path, filename="mystery.pdf", content_hash="hash-classify")

    async def failing_classify(file_path, client=None):
        raise RuntimeError("classification API timeout")

    monkeypatch.setattr(pipeline, "classify_document", failing_classify)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.NEEDS_ATTENTION
    assert "classification API timeout" in result.failure_reason


@pytest.mark.asyncio
async def test_ingest_document_marks_needs_attention_on_extraction_failure(session, monkeypatch, tmp_path):
    document = _make_document(session, tmp_path, filename="bad.pdf", content_hash="hash2")

    async def failing_extract_bill(file_path, client=None):
        raise ExtractionError("could not parse")

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline, "extract_bill", failing_extract_bill)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.NEEDS_ATTENTION
    assert "could not parse" in result.failure_reason


@pytest.mark.asyncio
async def test_ingest_document_skips_duplicate_transaction(session, monkeypatch, tmp_path):
    existing_document = _make_document(session, tmp_path, filename="first.pdf", content_hash="hash-a")
    existing_document.status = DocumentStatus.PROCESSED
    session.add(existing_document)
    session.commit()

    session.add(Transaction(
        document_id=existing_document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=87.32, currency="EUR", statement_period="2026-08",
    ))
    session.commit()

    new_document = _make_document(session, tmp_path, filename="duplicate.pdf", content_hash="hash-b")

    extracted = ExtractedBill(
        provider="EDP", category_hint="electricity", amount=87.32, currency="EUR",
        due_date=None, paid_date=None, statement_period="2026-08",
    )

    async def fake_extract_bill(file_path, client=None):
        return extracted

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline, "extract_bill", fake_extract_bill)

    result = await pipeline.ingest_document(session, new_document)

    assert result.status == DocumentStatus.PROCESSED
    assert "duplicate" in result.failure_reason
    transactions = session.exec(
        select(Transaction).where(Transaction.document_id == new_document.id)
    ).all()
    assert transactions == []


@pytest.mark.asyncio
async def test_ingest_document_marks_needs_attention_on_unexpected_extraction_error(session, monkeypatch, tmp_path):
    document = _make_document(session, tmp_path, filename="corrupt.pdf", content_hash="hash3")

    async def failing_extract_bill(file_path, client=None):
        raise RuntimeError("API timeout")

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline, "extract_bill", failing_extract_bill)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.NEEDS_ATTENTION
    assert "API timeout" in result.failure_reason


@pytest.mark.asyncio
async def test_ingest_document_marks_needs_attention_on_enrichment_failure(session, monkeypatch, tmp_path):
    document = _make_document(session, tmp_path, filename="bill.pdf", content_hash="hash4")

    extracted = ExtractedBill(
        provider="EDP", category_hint="electricity", amount=87.32, currency="EUR",
        due_date=date(2026, 9, 5), paid_date=None, statement_period="2026-08",
    )

    async def fake_extract_bill(file_path, client=None):
        return extracted

    async def failing_assess_and_update_wiki(session, document, transaction, client=None):
        raise RuntimeError("wiki assessment failed")

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline, "assess_and_update_wiki", failing_assess_and_update_wiki)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.NEEDS_ATTENTION
    assert result.status != DocumentStatus.PENDING
    assert "wiki assessment failed" in result.failure_reason

    transaction = session.exec(
        select(Transaction).where(Transaction.document_id == document.id)
    ).first()
    assert transaction is not None
    assert transaction.provider == "EDP"
    assert transaction.amount == 87.32


@pytest.mark.asyncio
async def test_ingest_statement_creates_one_transaction_per_line_item(session, monkeypatch, tmp_path):
    document = _make_document(session, tmp_path, filename="statement.pdf", content_hash="hash-stmt-1")

    extracted = ExtractedStatement(
        statement_period="2026-07",
        transactions=[
            ExtractedTransaction(
                transaction_date=date(2026, 7, 5), description="CONTINENTE MAFRA",
                amount=42.15, currency="EUR", transaction_type="debit", category_hint="groceries",
            ),
            ExtractedTransaction(
                transaction_date=date(2026, 7, 10), description="SALARIO EMPRESA X",
                amount=2200.0, currency="EUR", transaction_type="credit", category_hint="income",
            ),
            ExtractedTransaction(
                transaction_date=date(2026, 7, 12), description="TRANSFERENCIA PARA REVOLUT",
                amount=100.0, currency="EUR", transaction_type="transfer", category_hint="transfer",
            ),
        ],
    )

    async def fake_extract_statement_transactions(file_path, client=None):
        return extracted

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_statement)
    monkeypatch.setattr(pipeline, "extract_statement_transactions", fake_extract_statement_transactions)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.PROCESSED
    transactions = session.exec(
        select(Transaction).where(Transaction.document_id == document.id).order_by(Transaction.paid_date)
    ).all()
    assert len(transactions) == 3
    assert transactions[0].provider == "CONTINENTE MAFRA"
    assert transactions[0].category == Category.GROCERIES
    assert transactions[0].transaction_type == TransactionType.DEBIT
    assert transactions[0].statement_period == "2026-07"
    assert transactions[1].transaction_type == TransactionType.CREDIT
    assert transactions[1].category == Category.INCOME
    assert transactions[2].transaction_type == TransactionType.TRANSFER
    assert transactions[2].category == Category.TRANSFER


@pytest.mark.asyncio
async def test_ingest_statement_skips_dedup_todo_and_wiki(session, monkeypatch, tmp_path):
    document = _make_document(session, tmp_path, filename="statement2.pdf", content_hash="hash-stmt-2")

    extracted = ExtractedStatement(
        statement_period="2026-07",
        transactions=[
            ExtractedTransaction(
                transaction_date=date(2026, 7, 5), description="CONTINENTE MAFRA",
                amount=42.15, currency="EUR", transaction_type="debit", category_hint="groceries",
            ),
        ],
    )

    async def fake_extract_statement_transactions(file_path, client=None):
        return extracted

    def failing_generate_todo(session, transaction):
        raise AssertionError("generate_todo_for_transaction must not be called for statement transactions")

    async def failing_assess_and_update_wiki(session, document, transaction, client=None):
        raise AssertionError("assess_and_update_wiki must not be called for statement transactions")

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_statement)
    monkeypatch.setattr(pipeline, "extract_statement_transactions", fake_extract_statement_transactions)
    monkeypatch.setattr(pipeline, "generate_todo_for_transaction", failing_generate_todo)
    monkeypatch.setattr(pipeline, "assess_and_update_wiki", failing_assess_and_update_wiki)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.PROCESSED
    assert session.exec(select(Todo)).all() == []
    assert session.exec(select(WikiPage)).all() == []


@pytest.mark.asyncio
async def test_ingest_statement_marks_needs_attention_on_extraction_failure(session, monkeypatch, tmp_path):
    document = _make_document(session, tmp_path, filename="bad-statement.pdf", content_hash="hash-stmt-3")

    async def failing_extract_statement(file_path, client=None):
        raise RuntimeError("could not parse statement")

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_statement)
    monkeypatch.setattr(pipeline, "extract_statement_transactions", failing_extract_statement)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.NEEDS_ATTENTION
    assert "could not parse statement" in result.failure_reason


@pytest.mark.asyncio
async def test_ingest_statement_marks_needs_attention_on_bad_transaction_type_value(session, monkeypatch, tmp_path):
    """A transaction_type value outside debit/credit/transfer (an LLM
    mistake) must degrade to needs_attention, not crash the request — and
    must not leave a partially-committed set of transactions behind."""
    document = _make_document(session, tmp_path, filename="weird-statement.pdf", content_hash="hash-stmt-4")

    extracted = ExtractedStatement(
        statement_period="2026-07",
        transactions=[
            ExtractedTransaction(
                transaction_date=date(2026, 7, 5), description="CONTINENTE MAFRA",
                amount=42.15, currency="EUR", transaction_type="debit", category_hint="groceries",
            ),
            ExtractedTransaction(
                transaction_date=date(2026, 7, 6), description="MYSTERY LINE",
                amount=10.0, currency="EUR", transaction_type="not-a-real-type", category_hint="other_expense",
            ),
        ],
    )

    async def fake_extract_statement_transactions(file_path, client=None):
        return extracted

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_statement)
    monkeypatch.setattr(pipeline, "extract_statement_transactions", fake_extract_statement_transactions)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.NEEDS_ATTENTION
    transactions = session.exec(
        select(Transaction).where(Transaction.document_id == document.id)
    ).all()
    assert transactions == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL — `AttributeError: <module 'app.services.pipeline' ...> does not have the attribute 'classify_document'` (and similar, since `pipeline.py` hasn't been updated yet)

- [ ] **Step 3: Rewrite `app/services/pipeline.py`**

```python
"""Orchestrates the document ingestion pipeline: classify -> extract ->
categorize -> dedup -> persist -> todo -> wiki. Every ingestion channel
(manual upload, email, bank sync) calls ingest_document as its single entry
point."""

from __future__ import annotations

from sqlmodel import Session

from app.models.document import Document, DocumentStatus
from app.models.transaction import Transaction, TransactionType
from app.services.categorization import normalize_category
from app.services.dedup import find_duplicate_transaction
from app.services.extraction import (
    ExtractedBill,
    classify_document,
    extract_bill,
    extract_statement_transactions,
)
from app.services.todo_engine import generate_todo_for_transaction
from app.services.wiki_engine import assess_and_update_wiki


async def ingest_document(session: Session, document: Document) -> Document:
    """Run the full ingestion pipeline for a Document already saved to disk.
    Updates and persists the Document's status before returning it."""
    try:
        doc_type = await classify_document(document.file_path)
    except Exception as exc:
        # Broad by design: ingest_document is the ingestion boundary — any
        # classification failure must land the Document on needs_attention
        # with a reason, never propagate uncaught.
        return _mark_needs_attention(session, document, str(exc))

    if doc_type == "statement":
        return await _ingest_statement(session, document)
    return await _ingest_bill(session, document)


def _mark_needs_attention(session: Session, document: Document, reason: str) -> Document:
    document.status = DocumentStatus.NEEDS_ATTENTION
    document.failure_reason = reason
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


async def _ingest_bill(session: Session, document: Document) -> Document:
    try:
        extracted: ExtractedBill = await extract_bill(document.file_path)
    except Exception as exc:
        # Broad by design — see ingest_document's docstring: any extraction
        # failure (Anthropic SDK errors, malformed responses, anything
        # unanticipated) must land on needs_attention, never propagate
        # uncaught and leave the Document stuck at PENDING.
        return _mark_needs_attention(session, document, str(exc))

    duplicate = find_duplicate_transaction(
        session, provider=extracted.provider, statement_period=extracted.statement_period
    )
    if duplicate is not None:
        document.status = DocumentStatus.PROCESSED
        document.failure_reason = "duplicate — matched existing transaction"
        session.add(document)
        session.commit()
        session.refresh(document)
        return document

    transaction = Transaction(
        document_id=document.id,
        provider=extracted.provider,
        category=normalize_category(extracted.category_hint),
        transaction_type=TransactionType.DEBIT,
        amount=extracted.amount,
        currency=extracted.currency,
        due_date=extracted.due_date,
        paid_date=extracted.paid_date,
        statement_period=extracted.statement_period,
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    try:
        generate_todo_for_transaction(session, transaction)
        await assess_and_update_wiki(session, document, transaction)
    except Exception as exc:
        # The Transaction is already safely committed at this point — an
        # enrichment failure (todo generation or the wiki's Claude call /
        # response parsing) must not leave Document.status stuck at PENDING,
        # an inconsistent partial-success state invisible to the dashboard.
        return _mark_needs_attention(session, document, f"processed but enrichment failed: {exc}")

    document.status = DocumentStatus.PROCESSED
    document.failure_reason = None
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


async def _ingest_statement(session: Session, document: Document) -> Document:
    try:
        extracted = await extract_statement_transactions(document.file_path)
        for item in extracted.transactions:
            transaction = Transaction(
                document_id=document.id,
                provider=item.description,
                category=normalize_category(item.category_hint),
                transaction_type=TransactionType(item.transaction_type),
                amount=item.amount,
                currency=item.currency,
                paid_date=item.transaction_date,
                statement_period=extracted.statement_period,
            )
            session.add(transaction)
        session.commit()
    except Exception as exc:
        # Roll back any staged-but-uncommitted Transaction rows from a
        # partway-through failure (e.g. a bad transaction_type value on a
        # later line item) before marking needs_attention, so nothing
        # partially-ingested leaks into the next commit.
        session.rollback()
        return _mark_needs_attention(session, document, str(exc))

    # Statement-derived transactions are historical and already settled —
    # unlike bills, they never generate a to-do (no upcoming due date) or a
    # wiki assessment (no standing fact to record). Duplicate line items
    # sharing a provider/period within one statement are expected, not a
    # dedup signal; only whole-file re-upload (content-hash dedup, already
    # enforced before ingest_document is called) applies here.
    document.status = DocumentStatus.PROCESSED
    document.failure_reason = None
    session.add(document)
    session.commit()
    session.refresh(document)
    return document
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/pipeline.py tests/test_pipeline.py
git commit -m "feat: branch the ingestion pipeline on bill-vs-statement classification"
```

---

### Task 7: Dashboard spend excludes credits and transfers

**Files:**
- Modify: `app/services/dashboard_service.py`
- Modify: `tests/test_dashboard_service.py`

**Interfaces:**
- Consumes: `TransactionType` (Task 4)
- Produces: `app.services.dashboard_service.get_dashboard_data` — unchanged signature; `_spend_by_category` now filters to `transaction_type == DEBIT`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_service.py` (add `TransactionType` to the existing `from app.models.transaction import ...` import line):

```python
def test_spend_by_category_excludes_credits_and_transfers(session):
    document = Document(
        filename="statement.pdf", file_path="/tmp/statement.pdf", content_hash="h5",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    session.add(Transaction(
        document_id=document.id, provider="CONTINENTE", category=Category.GROCERIES,
        transaction_type=TransactionType.DEBIT, amount=40.0, currency="EUR",
        statement_period="2026-08",
    ))
    session.add(Transaction(
        document_id=document.id, provider="SALARIO", category=Category.INCOME,
        transaction_type=TransactionType.CREDIT, amount=2000.0, currency="EUR",
        statement_period="2026-08",
    ))
    session.add(Transaction(
        document_id=document.id, provider="REVOLUT TRANSFER", category=Category.TRANSFER,
        transaction_type=TransactionType.TRANSFER, amount=100.0, currency="EUR",
        statement_period="2026-08",
    ))
    session.commit()

    data = get_dashboard_data(session, today=date(2026, 8, 17))

    assert data.spend_this_month == {"groceries": 40.0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_service.py -v`
Expected: FAIL — `data.spend_this_month` includes `"income": 2000.0` and `"transfer": 100.0` alongside `"groceries": 40.0`

- [ ] **Step 3: Update `app/services/dashboard_service.py`**

Add `TransactionType` to the transaction model import, then update `_spend_by_category`:

```python
from app.models.transaction import Transaction, TransactionType
```

```python
def _spend_by_category(session: Session, period: str) -> dict[str, float]:
    statement = select(Transaction).where(
        Transaction.statement_period == period,
        Transaction.transaction_type == TransactionType.DEBIT,
    )
    totals: dict[str, float] = {}
    for txn in session.exec(statement):
        totals[txn.category.value] = totals.get(txn.category.value, 0.0) + txn.amount
    return totals
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_service.py -v`
Expected: PASS (4 tests) — the pre-existing `test_spend_totals_by_period` test creates `Transaction` rows without specifying `transaction_type`, which now defaults to `DEBIT`, so it keeps passing unchanged.

- [ ] **Step 5: Commit**

```bash
git add app/services/dashboard_service.py tests/test_dashboard_service.py
git commit -m "feat: exclude credits and transfers from dashboard spend-by-category"
```

---

### Task 8: Bill detail page renders every transaction, not one

**Files:**
- Modify: `app/routers/bills.py`
- Modify: `app/templates/bills/detail.html`
- Modify: `tests/test_bills_router.py`

**Interfaces:**
- Produces: `bill_detail` route now passes `transactions: list[Transaction]` to the template instead of an optional single `transaction`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bills_router.py`:

```python
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Category, Transaction, TransactionType


def test_bill_detail_renders_multiple_transactions(client, session):
    document = Document(
        filename="statement.pdf", file_path="/tmp/statement.pdf", content_hash="hstmt",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    session.add(Transaction(
        document_id=document.id, provider="CONTINENTE", category=Category.GROCERIES,
        transaction_type=TransactionType.DEBIT, amount=40.0, currency="EUR",
        statement_period="2026-08",
    ))
    session.add(Transaction(
        document_id=document.id, provider="SALARIO", category=Category.INCOME,
        transaction_type=TransactionType.CREDIT, amount=2000.0, currency="EUR",
        statement_period="2026-08",
    ))
    session.commit()

    response = client.get(f"/bills/{document.id}")

    assert response.status_code == 200
    assert "CONTINENTE" in response.text
    assert "SALARIO" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bills_router.py -v`
Expected: FAIL — today's `bill_detail` only fetches `.first()`, so only "CONTINENTE" (whichever row is first) would render, not both

- [ ] **Step 3: Update `app/routers/bills.py`**

Replace the `bill_detail` route:

```python
@router.get("/{document_id}")
async def bill_detail(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    transactions = session.exec(
        select(Transaction)
        .where(Transaction.document_id == document_id)
        .order_by(Transaction.paid_date, Transaction.id)
    ).all()
    return templates.TemplateResponse(
        request, "bills/detail.html", {"document": document, "transactions": transactions}
    )
```

- [ ] **Step 4: Update `app/templates/bills/detail.html`**

```html
{% extends "base.html" %}
{% block title %}{{ document.filename }} — Bills & Bank{% endblock %}
{% block content %}
<h1>{{ document.filename }}</h1>
<p>Status: {{ document.status.value }}</p>
{% if document.failure_reason %}
<p class="needs-attention">{{ document.failure_reason }}</p>
{% endif %}
{% if transactions %}
<table>
  <thead>
    <tr><th>Provider</th><th>Category</th><th>Type</th><th>Amount</th><th>Date</th><th>Statement period</th></tr>
  </thead>
  <tbody>
    {% for transaction in transactions %}
    <tr>
      <td>{{ transaction.provider }}</td>
      <td>{{ transaction.category.value }}</td>
      <td>{{ transaction.transaction_type.value }}</td>
      <td>{{ "%.2f"|format(transaction.amount) }} {{ transaction.currency }}</td>
      <td>{{ transaction.due_date or transaction.paid_date or "—" }}</td>
      <td>{{ transaction.statement_period or "—" }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_bills_router.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add app/routers/bills.py app/templates/bills/detail.html tests/test_bills_router.py
git commit -m "feat: render every transaction on the bill detail page, not just one"
```

---

### Task 9: End-to-end regression, dependency cleanup, full suite

**Files:**
- Modify: `requirements.txt`
- Modify: `tests/test_e2e_bill_flow.py`

**Interfaces:**
- Consumes: everything from Tasks 1–8. No new production code beyond the `requirements.txt` cleanup.

- [ ] **Step 1: Remove the now-unused PDF-to-image dependencies from `requirements.txt`**

Delete these two lines (no other code references them after Task 2):

```
pdfplumber>=0.11.4
pdf2image>=1.17.0
```

- [ ] **Step 2: Update `tests/test_e2e_bill_flow.py`'s existing bill-flow test**

Replace the `monkeypatch.setattr(pipeline_module, "ensure_image", lambda path: path)` line with a `classify_document` fake, and adjust the `fake_extract_bill` signature's parameter name for clarity (functionally identical, still positional):

```python
import io
import json
from datetime import date

import app.services.pipeline as pipeline_module
import app.services.wiki_engine as wiki_engine_module
from app.services.extraction import ExtractedBill, ExtractedStatement, ExtractedTransaction


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text

    async def create(self, **kwargs):
        return _FakeMessage(self._response_text)


class _FakeAnthropicClient:
    def __init__(self, response_text):
        self.messages = _FakeMessages(response_text)


async def _fake_classify_bill(file_path, client=None):
    return "bill"


async def _fake_classify_statement(file_path, client=None):
    return "statement"


def test_full_bill_ingestion_flow(client, monkeypatch):
    wiki_response = json.dumps({
        "wiki_worthy": True,
        "topic": "Electricity — provider & contract",
        "facts": {"provider": "EDP"},
    })

    async def fake_extract_bill(file_path, client=None):
        return ExtractedBill(
            provider="EDP", category_hint="electricity", amount=87.32, currency="EUR",
            due_date=date(2026, 9, 5), paid_date=None, statement_period="2026-08",
        )

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return await wiki_engine_module.assess_and_update_wiki(
            session, document, transaction, client=_FakeAnthropicClient(wiki_response)
        )

    monkeypatch.setattr(pipeline_module, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline_module, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline_module, "assess_and_update_wiki", fake_assess_and_update_wiki)

    upload_response = client.post(
        "/bills/upload",
        files={"file": ("edp-august.pdf", io.BytesIO(b"fake-pdf-bytes"), "application/pdf")},
        follow_redirects=False,
    )
    assert upload_response.status_code == 303

    dashboard_response = client.get("/")
    assert dashboard_response.status_code == 200
    assert "electricity" in dashboard_response.text.lower()
    assert "EDP" in dashboard_response.text

    todos_response = client.get("/todos")
    assert "EDP" in todos_response.text

    wiki_list_response = client.get("/wiki")
    assert "Electricity" in wiki_list_response.text


def test_full_statement_ingestion_flow(client, monkeypatch):
    async def fake_extract_statement_transactions(file_path, client=None):
        return ExtractedStatement(
            statement_period="2026-07",
            transactions=[
                ExtractedTransaction(
                    transaction_date=date(2026, 7, 5), description="CONTINENTE MAFRA",
                    amount=42.15, currency="EUR", transaction_type="debit", category_hint="groceries",
                ),
                ExtractedTransaction(
                    transaction_date=date(2026, 7, 10), description="SALARIO EMPRESA X",
                    amount=2200.0, currency="EUR", transaction_type="credit", category_hint="income",
                ),
            ],
        )

    monkeypatch.setattr(pipeline_module, "classify_document", _fake_classify_statement)
    monkeypatch.setattr(pipeline_module, "extract_statement_transactions", fake_extract_statement_transactions)

    upload_response = client.post(
        "/bills/upload",
        files={"file": ("santander-july.pdf", io.BytesIO(b"fake-pdf-bytes"), "application/pdf")},
        follow_redirects=False,
    )
    assert upload_response.status_code == 303
    document_url = upload_response.headers["location"]

    detail_response = client.get(document_url)
    assert detail_response.status_code == 200
    assert "CONTINENTE MAFRA" in detail_response.text
    assert "SALARIO EMPRESA X" in detail_response.text

    dashboard_response = client.get("/")
    assert dashboard_response.status_code == 200
    assert "groceries" in dashboard_response.text.lower()
    # The credit (income) line must not appear in the spend-this-month section total —
    # 2200.0 would be an unmistakable outlier if it leaked into spend.
    assert "2200.00" not in dashboard_response.text

    todos_response = client.get("/todos")
    assert "CONTINENTE" not in todos_response.text
    assert "SALARIO" not in todos_response.text

    wiki_list_response = client.get("/wiki")
    assert "No wiki pages yet." in wiki_list_response.text
```

- [ ] **Step 3: Run the new and existing e2e tests**

Run: `pytest tests/test_e2e_bill_flow.py -v`
Expected: PASS (2 tests)

- [ ] **Step 4: Reinstall dependencies and run the full suite**

```bash
pip install -r requirements.txt
pytest -q
```

Expected: every test in the suite passes, pristine output, with the total count higher than sub-project 1's 57 (this plan adds roughly 30 new tests across Tasks 1, 3, 5, 6, 7, 8, 9 while removing none).

- [ ] **Step 5: Commit**

```bash
git add requirements.txt tests/test_e2e_bill_flow.py
git commit -m "test: add end-to-end statement ingestion coverage, drop unused PDF-to-image deps"
```

---

## Self-Review Notes

- **Spec coverage:** native PDF document blocks (Tasks 1–2), classification (Task 3), statement extraction (Task 5), `TransactionType` + expanded `Category` + batch migration (Task 4), pipeline branching with dedup/todo/wiki skipped on the statement path (Task 6), dashboard DEBIT-only spend (Task 7), multi-transaction bill detail page (Task 8), full regression + dependency cleanup (Task 9). The spec's "Out of Scope" items (partial-item recovery, bulk-upload UI, wiki/todo/other-channel changes) are correctly absent from every task.
- **Placeholder scan:** no TBD/TODO markers; every step has runnable code, a concrete command, or an explicit manual-verification command (Task 4's migration check).
- **Type consistency:** `TransactionType`, `ExtractedStatement`, `ExtractedTransaction`, `classify_document`, `extract_statement_transactions` are used identically across every task that defines and consumes them — cross-checked against Task 6's full pipeline rewrite, which is the task most exposed to a drift between what Tasks 3–5 produce and what the pipeline expects.
- **Known risk flagged for the reviewer:** Task 4's migration is hand-written rather than autogenerated, specifically because Alembic's SQLite enum-diff detection is unreliable — Step 9's manual schema verification (both directions) is not optional and should not be skipped or waved through in review.
