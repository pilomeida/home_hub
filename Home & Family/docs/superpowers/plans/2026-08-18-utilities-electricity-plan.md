# Utilities Domain (Electricity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Utilities" nav tab (Electricity / Water / Telecom sub-tabs) to the Home & Family hub, with a new `UtilityReading` model that captures consumption + cost-breakdown detail beyond the generic bill/Transaction model, a pipeline extension that extracts this detail automatically from future electricity bills, and server-rendered table + chart views for Electricity (Water/Telecom stay empty-state for now).

**Architecture:** `UtilityReading` is a new table, deliberately independent of `Transaction` — every historical/future electricity bill still gets a normal `Transaction` (so the existing Bills dashboard spend-by-category view stays accurate) plus, additively, a `UtilityReading` row carrying the consumption/cost-component detail the generic model doesn't track. A second, focused Claude extraction call (`extract_utility_detail`) runs only when a bill's category is a utility category, only after the bill's own `Transaction` already succeeded, and its failure is pure enrichment loss — never a reason to mark the Document `needs_attention`.

**Tech Stack:** Same as the rest of the hub — FastAPI/SQLModel/Alembic/Jinja2/htmx, Claude Sonnet 5 for extraction, server-rendered HTML/CSS (no new JS charting dependency).

**Spec:** `docs/superpowers/specs/2026-08-18-utilities-electricity-design.md`

## Global Constraints

- Utility-detail extraction failure must NEVER mark the Document `needs_attention` or affect the bill's own `Transaction` — it is enrichment only, exactly like todo/wiki enrichment already degrades gracefully, but with a stricter policy (todo/wiki failure DOES mark `needs_attention`; utility-detail failure does not — this is intentional, per spec).
- `UtilityReading` rows are only created when the bill's `Transaction.category.value` is one of `"electricity"`, `"water"`, `"telecom"`.
- The historical import from `Electricity consumption.xlsx` is explicitly OUT OF SCOPE for this plan — a personal one-off script (same category as the bank-statement backfill script), to be written after this plan merges.
- No changes to `_ingest_bill`'s existing dedup/todo/wiki logic — utility-detail creation is strictly additive, inserted after the existing bill path's `Transaction` is already committed.
- Reuse `_MODEL` (`"claude-sonnet-5"`), `_parse_date`, and `strip_json_fences` already defined/imported in `app/services/extraction.py` — do not redefine them.
- Follow existing test conventions exactly: the `client`/`session` fixtures from `tests/conftest.py`, the `_FakeAnthropicClient`/`_FakeMessages`/`_FakeMessage` pattern already in `tests/test_extraction.py`, and the `_make_document` helper pattern already in `tests/test_pipeline.py`.

## File Structure

- Create: `app/models/utility_reading.py` — `UtilityType` enum, `UtilityReading` model
- Modify: `app/models/__init__.py` — register the new model
- Create: `alembic/versions/<hash>_add_utility_readings_table.py` — new table migration
- Modify: `app/services/extraction.py` — add `extract_utility_detail`
- Modify: `app/services/pipeline.py` — call it from `_ingest_bill`, create `UtilityReading` on success
- Create: `app/routers/utilities.py` — `/utilities`, `/utilities/{tab}`
- Modify: `app/main.py` — register the new router
- Create: `app/templates/utilities/tab.html` — table + chart imports + empty state
- Create: `app/templates/utilities/_bar_chart.html` — reusable Jinja macro for one bar-chart series
- Modify: `app/templates/base.html` — add the "Utilities" nav link, add subnav/chart CSS
- Create: `tests/test_utility_reading_model.py`
- Modify: `tests/test_extraction.py`
- Modify: `tests/test_pipeline.py`
- Create: `tests/test_utilities_router.py`
- Modify: `tests/test_e2e_bill_flow.py`

---

### Task 1: `UtilityReading` model and migration

**Files:**
- Create: `app/models/utility_reading.py`
- Modify: `app/models/__init__.py`
- Create: `alembic/versions/<new>_add_utility_readings_table.py`
- Test: `tests/test_utility_reading_model.py`

**Interfaces:**
- Produces: `app.models.utility_reading.UtilityType` (`ELECTRICITY` / `WATER` / `TELECOM`, `str, Enum`, lowercase values matching `Category`'s existing electricity/water/telecom values); `app.models.utility_reading.UtilityReading` (SQLModel table)

- [ ] **Step 1: Write the failing test**

Create `tests/test_utility_reading_model.py`:

```python
from datetime import date

from app.models.document import Document, DocumentSource
from app.models.utility_reading import UtilityReading, UtilityType


def test_create_and_read_utility_reading(session):
    document = Document(
        filename="edp-july.pdf", file_path="/tmp/edp-july.pdf",
        content_hash="hash-elec-1", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    reading = UtilityReading(
        document_id=document.id,
        utility_type=UtilityType.ELECTRICITY,
        period_label="2026-07",
        billing_period_start=date(2026, 6, 26),
        billing_period_end=date(2026, 7, 25),
        invoice_number="FA CO26/42 105",
        consumption_value=401.0,
        consumption_unit="kWh",
        cost_total=85.0,
        cost_per_unit=85.0 / 401.0,
        energy_cost=56.5,
        power_cost=4.54,
        fees_taxes_cost=12.99,
        vat_cost=10.97,
    )
    session.add(reading)
    session.commit()
    session.refresh(reading)

    fetched = session.get(UtilityReading, reading.id)
    assert fetched.utility_type == UtilityType.ELECTRICITY
    assert fetched.period_label == "2026-07"
    assert fetched.consumption_value == 401.0
    assert fetched.consumption_unit == "kWh"
    assert fetched.cost_total == 85.0
    assert fetched.energy_cost == 56.5


def test_utility_reading_optional_fields_default_to_none(session):
    document = Document(
        filename="water-bill.pdf", file_path="/tmp/water-bill.pdf",
        content_hash="hash-water-1", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    reading = UtilityReading(
        document_id=document.id,
        utility_type=UtilityType.WATER,
        period_label="2026-07",
        cost_total=20.0,
    )
    session.add(reading)
    session.commit()
    session.refresh(reading)

    fetched = session.get(UtilityReading, reading.id)
    assert fetched.billing_period_start is None
    assert fetched.invoice_number is None
    assert fetched.consumption_value is None
    assert fetched.cost_per_unit is None
    assert fetched.energy_cost is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_utility_reading_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.utility_reading'`

- [ ] **Step 3: Write `app/models/utility_reading.py`**

```python
"""UtilityReading: consumption + cost-breakdown detail for a utility bill,
beyond what the generic Transaction model tracks."""

from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class UtilityType(str, Enum):
    ELECTRICITY = "electricity"
    WATER = "water"
    TELECOM = "telecom"


class UtilityReading(SQLModel, table=True):
    __tablename__ = "utility_readings"

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", index=True)
    utility_type: UtilityType
    period_label: str = Field(index=True)
    billing_period_start: Optional[date] = None
    billing_period_end: Optional[date] = None
    invoice_number: Optional[str] = None
    consumption_value: Optional[float] = None
    consumption_unit: Optional[str] = None
    cost_total: float
    cost_per_unit: Optional[float] = None
    energy_cost: Optional[float] = None
    power_cost: Optional[float] = None
    fees_taxes_cost: Optional[float] = None
    vat_cost: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 4: Register the model**

Modify `app/models/__init__.py`, add after the `WikiChange, WikiPage` import line:

```python
from app.models.utility_reading import UtilityReading  # noqa: F401
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_utility_reading_model.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Generate and verify the migration**

This is a brand-new table with no enum-widening involved (unlike the `transactions.category` migration earlier in this project) — `alembic revision --autogenerate` is reliable here. Run:

```bash
alembic revision --autogenerate -m "add utility_readings table"
```

Open the generated file and confirm its `upgrade()`/`downgrade()` match this shape exactly (adjust naming/ordering if autogenerate produced something different — the table and column set below is authoritative, not the autogenerate output):

```python
def upgrade() -> None:
    op.create_table(
        "utility_readings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("utility_type", sa.Enum("ELECTRICITY", "WATER", "TELECOM", name="utilitytype"), nullable=False),
        sa.Column("period_label", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("billing_period_start", sa.Date(), nullable=True),
        sa.Column("billing_period_end", sa.Date(), nullable=True),
        sa.Column("invoice_number", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("consumption_value", sa.Float(), nullable=True),
        sa.Column("consumption_unit", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("cost_total", sa.Float(), nullable=False),
        sa.Column("cost_per_unit", sa.Float(), nullable=True),
        sa.Column("energy_cost", sa.Float(), nullable=True),
        sa.Column("power_cost", sa.Float(), nullable=True),
        sa.Column("fees_taxes_cost", sa.Float(), nullable=True),
        sa.Column("vat_cost", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_utility_readings_document_id"), "utility_readings", ["document_id"])
    op.create_index(op.f("ix_utility_readings_period_label"), "utility_readings", ["period_label"])


def downgrade() -> None:
    op.drop_index(op.f("ix_utility_readings_period_label"), table_name="utility_readings")
    op.drop_index(op.f("ix_utility_readings_document_id"), table_name="utility_readings")
    op.drop_table("utility_readings")
```

Apply it and verify: `alembic upgrade head`, inspect with `sqlite3 data/home_family.db ".schema utility_readings"`, then `alembic downgrade -1` and confirm the table is gone, then `alembic upgrade head` again to leave the DB at head.

- [ ] **Step 7: Commit**

```bash
git add app/models/utility_reading.py app/models/__init__.py alembic/versions/ tests/test_utility_reading_model.py
git commit -m "feat: add UtilityReading model and migration"
```

---

### Task 2: `extract_utility_detail`

**Files:**
- Modify: `app/services/extraction.py`
- Modify: `tests/test_extraction.py`

**Interfaces:**
- Consumes: `app.services.document_input.build_content_block`, `_MODEL`, `_parse_date`, `strip_json_fences` (all already in `extraction.py`)
- Produces: `app.services.extraction.ExtractedUtilityDetail` dataclass (`period_label: str`, `billing_period_start: Optional[date]`, `billing_period_end: Optional[date]`, `invoice_number: Optional[str]`, `consumption_value: Optional[float]`, `consumption_unit: Optional[str]`, `energy_cost: Optional[float]`, `power_cost: Optional[float]`, `fees_taxes_cost: Optional[float]`, `vat_cost: Optional[float]`); `app.services.extraction.UtilityDetailExtractionError(Exception)`; `app.services.extraction.extract_utility_detail(file_path: str, utility_type: str, client=None) -> ExtractedUtilityDetail` (async)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_extraction.py`:

```python
@pytest.mark.asyncio
async def test_extract_utility_detail_parses_valid_response(tmp_path):
    pdf_path = tmp_path / "electricity.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    response = json.dumps({
        "period_label": "2026-07",
        "billing_period_start": "2026-06-26",
        "billing_period_end": "2026-07-25",
        "invoice_number": "FA CO26/42 105",
        "consumption_value": 401,
        "consumption_unit": "kWh",
        "energy_cost": 56.5,
        "power_cost": 4.54,
        "fees_taxes_cost": 12.99,
        "vat_cost": 10.97,
    })
    client = _FakeAnthropicClient(response)

    result = await extract_utility_detail(str(pdf_path), "electricity", client=client)

    assert isinstance(result, ExtractedUtilityDetail)
    assert result.period_label == "2026-07"
    assert result.billing_period_start.isoformat() == "2026-06-26"
    assert result.consumption_value == 401.0
    assert result.consumption_unit == "kWh"
    assert result.energy_cost == 56.5
    assert result.vat_cost == 10.97


@pytest.mark.asyncio
async def test_extract_utility_detail_raises_on_malformed_json(tmp_path):
    pdf_path = tmp_path / "electricity.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient("not json")

    with pytest.raises(UtilityDetailExtractionError):
        await extract_utility_detail(str(pdf_path), "electricity", client=client)


@pytest.mark.asyncio
async def test_extract_utility_detail_raises_when_period_label_missing(tmp_path):
    pdf_path = tmp_path / "electricity.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient(json.dumps({"consumption_value": 401}))

    with pytest.raises(UtilityDetailExtractionError):
        await extract_utility_detail(str(pdf_path), "electricity", client=client)


@pytest.mark.asyncio
async def test_extract_utility_detail_handles_all_null_optional_fields(tmp_path):
    pdf_path = tmp_path / "electricity.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient(json.dumps({"period_label": "2026-07"}))

    result = await extract_utility_detail(str(pdf_path), "electricity", client=client)

    assert result.period_label == "2026-07"
    assert result.billing_period_start is None
    assert result.consumption_value is None
    assert result.energy_cost is None
```

Update the import block at the top of `tests/test_extraction.py`:

```python
from app.services.extraction import (
    ClassificationError,
    ExtractedBill,
    ExtractedStatement,
    ExtractedUtilityDetail,
    ExtractionError,
    StatementExtractionError,
    UtilityDetailExtractionError,
    classify_document,
    extract_bill,
    extract_statement_transactions,
    extract_utility_detail,
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_extraction.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Add to `app/services/extraction.py`** (after `extract_statement_transactions`, at the end of the file)

```python
_UTILITY_DETAIL_SYSTEM_PROMPT = """You extract consumption and cost-breakdown \
detail from a utility bill document (electricity, water, or telecom). \
Respond with ONLY a JSON object, no prose, matching this shape exactly:

{
  "period_label": "YYYY-MM — the calendar month holding the majority of \
days in this bill's billing period",
  "billing_period_start": "YYYY-MM-DD or null",
  "billing_period_end": "YYYY-MM-DD or null",
  "invoice_number": "string or null",
  "consumption_value": "number or null — the metered consumption amount \
(e.g. kWh for electricity, m3 for water, GB for telecom)",
  "consumption_unit": "string or null — the unit for consumption_value, \
e.g. kWh, m3, GB",
  "energy_cost": "number or null — the energy/consumption cost component, \
before power/fees/VAT, if the bill itemizes it separately",
  "power_cost": "number or null — a fixed power/capacity charge component, \
if the bill itemizes it separately",
  "fees_taxes_cost": "number or null — other fees and taxes, if itemized \
separately from VAT",
  "vat_cost": "number or null — VAT/tax component, if itemized separately"
}

period_label is required — always determine it even if other fields are \
uncertain. Use null liberally for any other field the bill doesn't clearly \
itemize; do not guess or approximate a value that isn't actually shown."""


class UtilityDetailExtractionError(Exception):
    pass


@dataclass
class ExtractedUtilityDetail:
    period_label: str
    billing_period_start: Optional[date]
    billing_period_end: Optional[date]
    invoice_number: Optional[str]
    consumption_value: Optional[float]
    consumption_unit: Optional[str]
    energy_cost: Optional[float]
    power_cost: Optional[float]
    fees_taxes_cost: Optional[float]
    vat_cost: Optional[float]


async def extract_utility_detail(
    file_path: str, utility_type: str, client: Optional[AsyncAnthropic] = None
) -> ExtractedUtilityDetail:
    """Extract consumption + cost-breakdown detail from a utility bill
    document. Enrichment only — callers must treat failure as non-fatal to
    the underlying bill's own processing."""
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    content_block = build_content_block(file_path)

    message = await anthropic_client.messages.create(
        model=_MODEL,
        max_tokens=1024,
        thinking={"type": "disabled"},
        system=_UTILITY_DETAIL_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,
                    {
                        "type": "text",
                        "text": f"Extract the {utility_type} consumption/cost detail as JSON.",
                    },
                ],
            }
        ],
    )

    try:
        raw_text = strip_json_fences(message.content[0].text)
        data = json.loads(raw_text)
        return ExtractedUtilityDetail(
            period_label=data["period_label"],
            billing_period_start=_parse_date(data.get("billing_period_start")),
            billing_period_end=_parse_date(data.get("billing_period_end")),
            invoice_number=data.get("invoice_number"),
            consumption_value=(
                float(data["consumption_value"]) if data.get("consumption_value") is not None else None
            ),
            consumption_unit=data.get("consumption_unit"),
            energy_cost=(float(data["energy_cost"]) if data.get("energy_cost") is not None else None),
            power_cost=(float(data["power_cost"]) if data.get("power_cost") is not None else None),
            fees_taxes_cost=(
                float(data["fees_taxes_cost"]) if data.get("fees_taxes_cost") is not None else None
            ),
            vat_cost=(float(data["vat_cost"]) if data.get("vat_cost") is not None else None),
        )
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise UtilityDetailExtractionError(f"Could not parse utility detail response: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_extraction.py -v`
Expected: PASS (17 tests — 13 existing + 4 new)

- [ ] **Step 5: Commit**

```bash
git add app/services/extraction.py tests/test_extraction.py
git commit -m "feat: add extract_utility_detail for consumption/cost-breakdown extraction"
```

---

### Task 3: Pipeline integration

**Files:**
- Modify: `app/services/pipeline.py`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `extract_utility_detail`, `ExtractedUtilityDetail` (Task 2); `UtilityReading`, `UtilityType` (Task 1)
- Produces: no new public interface — `_ingest_bill` gains a strictly additive enrichment step

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:

```python
from app.models.utility_reading import UtilityReading, UtilityType
from app.services.extraction import ExtractedUtilityDetail


@pytest.mark.asyncio
async def test_ingest_bill_creates_utility_reading_for_electricity_category(session, monkeypatch, tmp_path):
    document = _make_document(session, tmp_path, filename="edp.pdf", content_hash="hash-util-1")

    extracted = ExtractedBill(
        provider="EDP", category_hint="electricity", amount=85.0, currency="EUR",
        due_date=None, paid_date=None, statement_period="2026-07",
    )

    async def fake_extract_bill(file_path, client=None):
        return extracted

    async def fake_extract_utility_detail(file_path, utility_type, client=None):
        return ExtractedUtilityDetail(
            period_label="2026-07", billing_period_start=date(2026, 6, 26),
            billing_period_end=date(2026, 7, 25), invoice_number="FA CO26/42 105",
            consumption_value=401.0, consumption_unit="kWh",
            energy_cost=56.5, power_cost=4.54, fees_taxes_cost=12.99, vat_cost=10.97,
        )

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return None

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline, "extract_utility_detail", fake_extract_utility_detail)
    monkeypatch.setattr(pipeline, "assess_and_update_wiki", fake_assess_and_update_wiki)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.PROCESSED
    reading = session.exec(
        select(UtilityReading).where(UtilityReading.document_id == document.id)
    ).first()
    assert reading is not None
    assert reading.utility_type == UtilityType.ELECTRICITY
    assert reading.period_label == "2026-07"
    assert reading.consumption_value == 401.0
    assert reading.cost_total == 85.0
    assert reading.cost_per_unit == pytest.approx(85.0 / 401.0)


@pytest.mark.asyncio
async def test_ingest_bill_utility_detail_failure_does_not_mark_needs_attention(session, monkeypatch, tmp_path):
    document = _make_document(session, tmp_path, filename="edp2.pdf", content_hash="hash-util-2")

    extracted = ExtractedBill(
        provider="EDP", category_hint="electricity", amount=85.0, currency="EUR",
        due_date=None, paid_date=None, statement_period="2026-07",
    )

    async def fake_extract_bill(file_path, client=None):
        return extracted

    async def failing_extract_utility_detail(file_path, utility_type, client=None):
        raise RuntimeError("utility model call failed")

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return None

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline, "extract_utility_detail", failing_extract_utility_detail)
    monkeypatch.setattr(pipeline, "assess_and_update_wiki", fake_assess_and_update_wiki)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.PROCESSED
    assert session.exec(
        select(UtilityReading).where(UtilityReading.document_id == document.id)
    ).first() is None
    transaction = session.exec(
        select(Transaction).where(Transaction.document_id == document.id)
    ).first()
    assert transaction is not None
    assert transaction.amount == 85.0


@pytest.mark.asyncio
async def test_ingest_bill_skips_utility_reading_for_non_utility_category(session, monkeypatch, tmp_path):
    document = _make_document(session, tmp_path, filename="groceries.pdf", content_hash="hash-util-3")

    extracted = ExtractedBill(
        provider="Continente", category_hint="groceries", amount=42.0, currency="EUR",
        due_date=None, paid_date=None, statement_period="2026-07",
    )

    async def fake_extract_bill(file_path, client=None):
        return extracted

    async def failing_extract_utility_detail(file_path, utility_type, client=None):
        raise AssertionError("extract_utility_detail must not be called for non-utility categories")

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return None

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline, "extract_utility_detail", failing_extract_utility_detail)
    monkeypatch.setattr(pipeline, "assess_and_update_wiki", fake_assess_and_update_wiki)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.PROCESSED
    assert session.exec(
        select(UtilityReading).where(UtilityReading.document_id == document.id)
    ).first() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL — `AttributeError: <module 'app.services.pipeline' ...> does not have the attribute 'extract_utility_detail'`

- [ ] **Step 3: Modify `app/services/pipeline.py`**

Update the extraction import block:

```python
from app.services.extraction import (
    ExtractedBill,
    classify_document,
    extract_bill,
    extract_statement_transactions,
    extract_utility_detail,
)
```

Add the model import:

```python
from app.models.utility_reading import UtilityReading, UtilityType
```

Add a module-level constant near the top (after the imports):

```python
_UTILITY_CATEGORY_VALUES = {"electricity", "water", "telecom"}
```

In `_ingest_bill`, insert this block right after `session.refresh(transaction)` (i.e. between the existing `session.add(transaction)` / `session.commit()` / `session.refresh(transaction)` block and the existing `try: generate_todo_for_transaction(...)` block):

```python
    if transaction.category.value in _UTILITY_CATEGORY_VALUES:
        try:
            detail = await extract_utility_detail(document.file_path, transaction.category.value)
            reading = UtilityReading(
                document_id=document.id,
                utility_type=UtilityType(transaction.category.value),
                period_label=detail.period_label,
                billing_period_start=detail.billing_period_start,
                billing_period_end=detail.billing_period_end,
                invoice_number=detail.invoice_number,
                consumption_value=detail.consumption_value,
                consumption_unit=detail.consumption_unit,
                cost_total=transaction.amount,
                cost_per_unit=(
                    transaction.amount / detail.consumption_value
                    if detail.consumption_value
                    else None
                ),
                energy_cost=detail.energy_cost,
                power_cost=detail.power_cost,
                fees_taxes_cost=detail.fees_taxes_cost,
                vat_cost=detail.vat_cost,
            )
            session.add(reading)
            session.commit()
        except Exception as exc:
            # Utility detail is enrichment, not a requirement: a failure here
            # must not affect the bill's own Transaction/Document outcome —
            # deliberately looser than the todo/wiki block below (which DOES
            # mark needs_attention on failure), since utility tracking is a
            # nice-to-have layered on top of an already-successful bill.
            session.rollback()
            print(f"utility detail extraction failed for document {document.id}: {exc}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS (13 tests — 10 existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add app/services/pipeline.py tests/test_pipeline.py
git commit -m "feat: create UtilityReading from bill ingestion for utility categories"
```

---

### Task 4: Utilities router

**Files:**
- Create: `app/routers/utilities.py`
- Modify: `app/main.py`
- Test: `tests/test_utilities_router.py`

**Interfaces:**
- Consumes: `UtilityReading`, `UtilityType` (Task 1)
- Produces: `GET /utilities` (redirect to `/utilities/electricity`), `GET /utilities/{tab}` for `tab` in `{"electricity", "water", "telecom"}` (404 otherwise), rendering `utilities/tab.html` with context `{"active_tab": tab, "readings": [...], "charts": {...}}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_utilities_router.py`:

```python
from app.models.document import Document, DocumentSource
from app.models.utility_reading import UtilityReading, UtilityType


def _make_document(session, content_hash):
    document = Document(
        filename="edp.pdf", file_path="/tmp/edp.pdf", content_hash=content_hash,
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def test_utilities_root_redirects_to_electricity(client):
    response = client.get("/utilities", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/utilities/electricity"


def test_unknown_tab_returns_404(client):
    response = client.get("/utilities/gas")
    assert response.status_code == 404


def test_water_tab_renders_empty_state_with_no_data(client):
    response = client.get("/utilities/water")
    assert response.status_code == 200
    assert "No data yet" in response.text


def test_electricity_tab_renders_readings(client, session):
    document = _make_document(session, "hash-elec-1")
    session.add(UtilityReading(
        document_id=document.id, utility_type=UtilityType.ELECTRICITY,
        period_label="2026-07", consumption_value=401.0, consumption_unit="kWh",
        cost_total=85.0, cost_per_unit=85.0 / 401.0,
        energy_cost=56.5, power_cost=4.54, fees_taxes_cost=12.99, vat_cost=10.97,
    ))
    session.commit()

    response = client.get("/utilities/electricity")

    assert response.status_code == 200
    assert "1 reading" in response.text
```

Note: this test intentionally checks only the placeholder's reading-count text (`"1 reading(s) for electricity."`), not `period_label`/`consumption_value` content — the placeholder template from Step 5 below doesn't render those fields yet. Task 5 replaces the template with the real table/chart rendering and adds its own test asserting the actual data content (`"2026-07"`, `"401"`) against that real output — don't pull those assertions forward into this task's test, they'd fail against the placeholder.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_utilities_router.py -v`
Expected: FAIL — `404` for all routes (router doesn't exist yet, so `/utilities*` isn't registered)

- [ ] **Step 3: Create `app/routers/utilities.py`**

```python
"""Routes for the Utilities domain (Electricity / Water / Telecom)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.models.utility_reading import UtilityReading, UtilityType

router = APIRouter(prefix="/utilities", tags=["utilities"])
templates = Jinja2Templates(directory="app/templates")

_VALID_TABS = {
    "electricity": UtilityType.ELECTRICITY,
    "water": UtilityType.WATER,
    "telecom": UtilityType.TELECOM,
}


def _build_charts(readings: list[UtilityReading]) -> dict:
    def series(field: str) -> dict:
        rows = [(r.period_label, getattr(r, field)) for r in readings]
        values = [v for _, v in rows if v is not None]
        return {"rows": rows, "max": max(values) if values else 0}

    return {
        "consumption": series("consumption_value"),
        "cost_total": series("cost_total"),
        "cost_per_unit": series("cost_per_unit"),
        "energy_cost": series("energy_cost"),
        "power_cost": series("power_cost"),
        "fees_taxes_cost": series("fees_taxes_cost"),
        "vat_cost": series("vat_cost"),
    }


@router.get("")
async def utilities_root():
    return RedirectResponse("/utilities/electricity")


@router.get("/{tab}")
async def utility_tab(request: Request, tab: str, session: Session = Depends(get_session)):
    if tab not in _VALID_TABS:
        raise HTTPException(status_code=404, detail="Unknown utility tab")

    readings = session.exec(
        select(UtilityReading)
        .where(UtilityReading.utility_type == _VALID_TABS[tab])
        .order_by(UtilityReading.period_label)
    ).all()

    return templates.TemplateResponse(
        request,
        "utilities/tab.html",
        {"active_tab": tab, "readings": readings, "charts": _build_charts(readings)},
    )
```

- [ ] **Step 4: Register the router**

Modify `app/main.py`: change the router import line to include `utilities`, and add the include call:

```python
from app.routers import bills, dashboard, todos, utilities, wiki  # noqa: E402

app.include_router(dashboard.router)
app.include_router(bills.router)
app.include_router(todos.router)
app.include_router(utilities.router)
app.include_router(wiki.router)
```

- [ ] **Step 5: Create a minimal placeholder template so routes don't 500**

Create `app/templates/utilities/tab.html` with placeholder content for now (Task 5 replaces it with the real table/chart rendering):

```html
{% extends "base.html" %}
{% block title %}Utilities — Home & Family{% endblock %}
{% block content %}
<h1>Utilities</h1>
{% if readings %}
<p>{{ readings | length }} reading(s) for {{ active_tab }}.</p>
{% else %}
<p>No data yet.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_utilities_router.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add app/routers/utilities.py app/main.py app/templates/utilities/tab.html tests/test_utilities_router.py
git commit -m "feat: add Utilities router with Electricity/Water/Telecom tabs"
```

---

### Task 5: Templates — nav link, table, and bar charts

**Files:**
- Modify: `app/templates/base.html`
- Modify: `app/templates/utilities/tab.html`
- Create: `app/templates/utilities/_bar_chart.html`
- Modify: `tests/test_utilities_router.py`

**Interfaces:**
- Consumes: `charts` dict from Task 4's `_build_charts` (`{"consumption": {"rows": [(period_label, value), ...], "max": number}, "cost_total": {...}, "cost_per_unit": {...}, "energy_cost": {...}, "power_cost": {...}, "fees_taxes_cost": {...}, "vat_cost": {...}}`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_utilities_router.py`:

```python
def test_electricity_tab_renders_charts(client, session):
    document = _make_document(session, "hash-elec-2")
    session.add(UtilityReading(
        document_id=document.id, utility_type=UtilityType.ELECTRICITY,
        period_label="2026-07", consumption_value=401.0, consumption_unit="kWh",
        cost_total=85.0, cost_per_unit=85.0 / 401.0,
        energy_cost=56.5, power_cost=4.54, fees_taxes_cost=12.99, vat_cost=10.97,
    ))
    session.commit()

    response = client.get("/utilities/electricity")

    assert response.status_code == 200
    assert "2026-07" in response.text
    assert "401" in response.text
    assert "Consumption per month" in response.text
    assert "chart-bar-fill" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_utilities_router.py::test_electricity_tab_renders_charts -v`
Expected: FAIL — `"Consumption per month" not in response.text` (placeholder template from Task 4 doesn't render charts)

- [ ] **Step 3: Create `app/templates/utilities/_bar_chart.html`**

```html
{% macro bar_chart(title, chart, unit='') %}
<div class="chart">
  <h3>{{ title }}</h3>
  {% for label, value in chart.rows %}
  <div class="chart-row">
    <span class="chart-label">{{ label }}</span>
    <div class="chart-bar-track">
      {% if value is not none and chart.max %}
      <div class="chart-bar-fill" style="width: {{ (value / chart.max * 100) | round(1) }}%;"></div>
      {% endif %}
    </div>
    <span class="chart-value">{{ "%.2f"|format(value) if value is not none else "—" }}{{ unit }}</span>
  </div>
  {% endfor %}
</div>
{% endmacro %}
```

- [ ] **Step 4: Replace `app/templates/utilities/tab.html`**

```html
{% extends "base.html" %}
{% import "utilities/_bar_chart.html" as charts_macro %}
{% block title %}Utilities — Home & Family{% endblock %}
{% block content %}
<h1>Utilities</h1>
<nav class="subnav">
  <a href="/utilities/electricity" class="{{ 'active' if active_tab == 'electricity' else '' }}">Electricity</a>
  <a href="/utilities/water" class="{{ 'active' if active_tab == 'water' else '' }}">Water</a>
  <a href="/utilities/telecom" class="{{ 'active' if active_tab == 'telecom' else '' }}">Telecom</a>
</nav>

{% if readings %}
<table>
  <thead>
    <tr>
      <th>Month</th><th>Invoice</th><th>Consumption</th><th>Cost</th><th>Cost/unit</th>
      <th>Energy</th><th>Power</th><th>Fees & taxes</th><th>VAT</th>
    </tr>
  </thead>
  <tbody>
    {% for reading in readings %}
    <tr>
      <td>{{ reading.period_label }}</td>
      <td>{{ reading.invoice_number or "—" }}</td>
      <td>{{ reading.consumption_value if reading.consumption_value is not none else "—" }} {{ reading.consumption_unit or "" }}</td>
      <td>{{ "%.2f"|format(reading.cost_total) }}</td>
      <td>{{ "%.4f"|format(reading.cost_per_unit) if reading.cost_per_unit is not none else "—" }}</td>
      <td>{{ "%.2f"|format(reading.energy_cost) if reading.energy_cost is not none else "—" }}</td>
      <td>{{ "%.2f"|format(reading.power_cost) if reading.power_cost is not none else "—" }}</td>
      <td>{{ "%.2f"|format(reading.fees_taxes_cost) if reading.fees_taxes_cost is not none else "—" }}</td>
      <td>{{ "%.2f"|format(reading.vat_cost) if reading.vat_cost is not none else "—" }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>

{{ charts_macro.bar_chart("Consumption per month", charts.consumption, " " ~ (readings[0].consumption_unit or "")) }}
{{ charts_macro.bar_chart("Cost per month (€)", charts.cost_total, " €") }}
{{ charts_macro.bar_chart("Effective cost per unit (€)", charts.cost_per_unit, " €") }}
{{ charts_macro.bar_chart("Energy cost per month (€)", charts.energy_cost, " €") }}
{{ charts_macro.bar_chart("Power cost per month (€)", charts.power_cost, " €") }}
{{ charts_macro.bar_chart("Fees & taxes per month (€)", charts.fees_taxes_cost, " €") }}
{{ charts_macro.bar_chart("VAT per month (€)", charts.vat_cost, " €") }}

{% else %}
<p>No data yet.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Add subnav and chart CSS to `app/templates/base.html`**

Add these rules inside the existing `<style>` block (after the existing `.recently-changed` rule):

```css
    nav.subnav { width: auto; padding: 0; margin-bottom: 1rem; border-right: none; display: flex; gap: 1rem; }
    nav.subnav a { display: inline; margin-bottom: 0; }
    nav.subnav a.active { font-weight: bold; text-decoration: underline; }
    .chart { margin-bottom: 1.5rem; }
    .chart h3 { margin-bottom: 0.5rem; font-size: 1rem; }
    .chart-row { display: flex; align-items: center; gap: 0.5rem; font-size: 0.85rem; margin-bottom: 0.15rem; }
    .chart-label { width: 4.5rem; flex-shrink: 0; }
    .chart-bar-track { flex: 1; background: #f0f0f0; height: 1rem; }
    .chart-bar-fill { background: #3a6ea5; height: 100%; }
    .chart-value { width: 5rem; text-align: right; flex-shrink: 0; }
```

Add the nav link in the sidebar `<nav>` block, after `Bills & Bank`:

```html
    <a href="/utilities/electricity">Utilities</a>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_utilities_router.py -v`
Expected: PASS (6 tests — 4 existing + 2 new)

- [ ] **Step 7: Commit**

```bash
git add app/templates/base.html app/templates/utilities/tab.html app/templates/utilities/_bar_chart.html tests/test_utilities_router.py
git commit -m "feat: render Electricity table and bar charts, add Utilities nav link"
```

---

### Task 6: End-to-end regression

**Files:**
- Modify: `tests/test_e2e_bill_flow.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5. No new production code.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_e2e_bill_flow.py`:

```python
def test_electricity_bill_upload_appears_in_utilities_tab(client, monkeypatch):
    async def fake_classify_bill(file_path, client=None):
        return "bill"

    async def fake_extract_bill(file_path, client=None):
        return ExtractedBill(
            provider="EDP", category_hint="electricity", amount=85.0, currency="EUR",
            due_date=None, paid_date=None, statement_period="2026-07",
        )

    async def fake_extract_utility_detail(file_path, utility_type, client=None):
        return ExtractedUtilityDetail(
            period_label="2026-07", billing_period_start=None, billing_period_end=None,
            invoice_number="FA CO26/42 105", consumption_value=401.0, consumption_unit="kWh",
            energy_cost=56.5, power_cost=4.54, fees_taxes_cost=12.99, vat_cost=10.97,
        )

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return None

    monkeypatch.setattr(pipeline_module, "classify_document", fake_classify_bill)
    monkeypatch.setattr(pipeline_module, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline_module, "extract_utility_detail", fake_extract_utility_detail)
    monkeypatch.setattr(pipeline_module, "assess_and_update_wiki", fake_assess_and_update_wiki)

    upload_response = client.post(
        "/bills/upload",
        files={"file": ("edp-july.pdf", io.BytesIO(b"fake-pdf-bytes"), "application/pdf")},
        follow_redirects=False,
    )
    assert upload_response.status_code == 303

    electricity_response = client.get("/utilities/electricity")
    assert electricity_response.status_code == 200
    assert "2026-07" in electricity_response.text
    assert "401" in electricity_response.text

    dashboard_response = client.get("/")
    assert dashboard_response.status_code == 200
    assert "electricity" in dashboard_response.text.lower()
```

Add `ExtractedUtilityDetail` to the file's existing `from app.services.extraction import (...)` import line.

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_e2e_bill_flow.py -v`
Expected: PASS (3 tests — 2 existing + 1 new)

- [ ] **Step 3: Run the full suite**

Run: `pytest -q`
Expected: every test passes, total count higher than this plan's starting baseline of 106 (this plan adds roughly 15 new tests across Tasks 1, 2, 3, 5, 6).

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_bill_flow.py
git commit -m "test: add end-to-end electricity-bill-to-Utilities-tab coverage"
```

---

## Self-Review Notes

- **Spec coverage:** `UtilityReading` model (Task 1), pipeline extension with enrichment-only failure handling (Task 3), historical import explicitly out of scope (Global Constraints, matching the spec), Utilities nav tab with Electricity/Water/Telecom sub-tabs (Tasks 4–5), CSS-only bar charts with no new JS dependency (Task 5), full regression (Task 6). All spec sections have a corresponding task.
- **Placeholder scan:** no TBD/TODO markers; every step has runnable code or a concrete command.
- **Type consistency:** `ExtractedUtilityDetail`, `UtilityReading`, `UtilityType`, `extract_utility_detail` are used identically across every task that defines and consumes them — cross-checked Task 3 (pipeline, the task most exposed to drift between Tasks 1/2's definitions and their consumption) against both.
- **Known risk flagged for the reviewer:** Task 3's utility-detail failure handling is deliberately looser than the existing todo/wiki enrichment block in the same function (silent pass vs. `needs_attention`) — this is intentional per the spec, not an inconsistency to "fix" toward matching the todo/wiki block's stricter policy.
