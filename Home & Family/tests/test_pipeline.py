from datetime import date

import pytest
from sqlmodel import select

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Category, Transaction, TransactionType
from app.models.todo import Todo
from app.models.wiki import WikiPage
from app.services import pipeline
from app.services.extraction import ExtractedBill, ExtractedStatement, ExtractedTransaction, ExtractionError


@pytest.fixture(autouse=True)
def _stub_classify_transaction(monkeypatch):
    """Every pre-existing test in this file predates classification-engine
    wiring and doesn't expect an LLM call for merchant resolution. Stub it
    to a no-op by default; the two tests that actually verify
    classify_transaction gets called override this locally with their own
    monkeypatch.setattr call, which simply takes effect after this one."""
    async def _noop_classify_transaction(session, transaction, client=None):
        return None
    monkeypatch.setattr(pipeline, "classify_transaction", _noop_classify_transaction)


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


@pytest.mark.asyncio
async def test_ingest_statement_normalizes_mixed_case_and_whitespace_transaction_type(
    session, monkeypatch, tmp_path
):
    """A capitalized or whitespace-padded transaction_type from Claude (e.g.
    'Debit' or ' credit ') should normalize like category_hint does, not
    discard the whole statement to needs_attention."""
    document = _make_document(session, tmp_path, filename="messy-type-statement.pdf", content_hash="hash-stmt-5")

    extracted = ExtractedStatement(
        statement_period="2026-07",
        transactions=[
            ExtractedTransaction(
                transaction_date=date(2026, 7, 5), description="CONTINENTE MAFRA",
                amount=42.15, currency="EUR", transaction_type="Debit", category_hint="groceries",
            ),
            ExtractedTransaction(
                transaction_date=date(2026, 7, 10), description="SALARIO EMPRESA X",
                amount=2200.0, currency="EUR", transaction_type=" credit ", category_hint="income",
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
    assert len(transactions) == 2
    assert transactions[0].transaction_type == TransactionType.DEBIT
    assert transactions[1].transaction_type == TransactionType.CREDIT


@pytest.mark.asyncio
async def test_ingest_statement_stores_absolute_value_of_negative_amount(session, monkeypatch, tmp_path):
    """A sign slip from the model (negative amount for a debit) must not
    propagate — Transaction.amount should always be stored as a positive
    magnitude; direction lives in transaction_type."""
    document = _make_document(session, tmp_path, filename="negative-amount-statement.pdf", content_hash="hash-stmt-6")

    extracted = ExtractedStatement(
        statement_period="2026-07",
        transactions=[
            ExtractedTransaction(
                transaction_date=date(2026, 7, 5), description="CONTINENTE MAFRA",
                amount=-42.15, currency="EUR", transaction_type="debit", category_hint="groceries",
            ),
        ],
    )

    async def fake_extract_statement_transactions(file_path, client=None):
        return extracted

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_statement)
    monkeypatch.setattr(pipeline, "extract_statement_transactions", fake_extract_statement_transactions)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.PROCESSED
    transaction = session.exec(
        select(Transaction).where(Transaction.document_id == document.id)
    ).first()
    assert transaction is not None
    assert transaction.amount == 42.15


@pytest.mark.asyncio
async def test_ingest_document_sets_doc_type_on_document(session, monkeypatch, tmp_path):
    """ingest_document persists the classification result onto the Document
    itself, so downstream dedup queries can distinguish bill-derived
    transactions from statement line items."""
    bill_document = _make_document(session, tmp_path, filename="bill.pdf", content_hash="hash-doctype-bill")

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

    result = await pipeline.ingest_document(session, bill_document)
    assert result.doc_type == "bill"

    statement_document = _make_document(session, tmp_path, filename="statement.pdf", content_hash="hash-doctype-stmt")
    extracted_statement = ExtractedStatement(statement_period="2026-07", transactions=[])

    async def fake_extract_statement_transactions(file_path, client=None):
        return extracted_statement

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_statement)
    monkeypatch.setattr(pipeline, "extract_statement_transactions", fake_extract_statement_transactions)

    result2 = await pipeline.ingest_document(session, statement_document)
    assert result2.doc_type == "statement"


@pytest.mark.asyncio
async def test_ingest_document_bill_not_falsely_deduped_by_statement_line_item(session, monkeypatch, tmp_path):
    """Regression test for the bug the reviewer found: a statement line item
    sharing provider/period with a genuine, unrelated bill must NOT cause
    the bill to be silently marked as a duplicate. Ingest a statement
    containing an 'EDP' line item for 2026-07, then ingest a real EDP bill
    for the same period — the bill must produce a real Transaction and end
    up PROCESSED without a duplicate failure_reason."""
    statement_document = _make_document(session, tmp_path, filename="statement.pdf", content_hash="hash-regr-stmt")

    extracted_statement = ExtractedStatement(
        statement_period="2026-07",
        transactions=[
            ExtractedTransaction(
                transaction_date=date(2026, 7, 8), description="EDP",
                amount=87.32, currency="EUR", transaction_type="debit", category_hint="electricity",
            ),
        ],
    )

    async def fake_extract_statement_transactions(file_path, client=None):
        return extracted_statement

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_statement)
    monkeypatch.setattr(pipeline, "extract_statement_transactions", fake_extract_statement_transactions)

    statement_result = await pipeline.ingest_document(session, statement_document)
    assert statement_result.status == DocumentStatus.PROCESSED

    bill_document = _make_document(session, tmp_path, filename="edp-bill.pdf", content_hash="hash-regr-bill")

    extracted_bill = ExtractedBill(
        provider="EDP", category_hint="electricity", amount=87.32, currency="EUR",
        due_date=date(2026, 8, 5), paid_date=None, statement_period="2026-07",
    )

    async def fake_extract_bill(file_path, client=None):
        return extracted_bill

    async def fake_extract_utility_detail(file_path, utility_type, client=None):
        return ExtractedUtilityDetail(
            period_label="2026-07", billing_period_start=None, billing_period_end=None,
            invoice_number=None, consumption_value=None, consumption_unit=None,
            energy_cost=None, power_cost=None, fees_taxes_cost=None, vat_cost=None,
        )

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return None

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline, "extract_utility_detail", fake_extract_utility_detail)
    monkeypatch.setattr(pipeline, "assess_and_update_wiki", fake_assess_and_update_wiki)

    bill_result = await pipeline.ingest_document(session, bill_document)

    assert bill_result.status == DocumentStatus.PROCESSED
    assert bill_result.failure_reason is None
    bill_transaction = session.exec(
        select(Transaction).where(Transaction.document_id == bill_document.id)
    ).first()
    assert bill_transaction is not None
    assert bill_transaction.provider == "EDP"
    assert bill_transaction.amount == 87.32


@pytest.mark.asyncio
async def test_ingest_document_true_bill_duplicate_still_detected(session, monkeypatch, tmp_path):
    """The original behavior this dedup function exists for must keep
    working: uploading the same bill provider/period twice must mark the
    second upload as a duplicate, producing no second Transaction."""
    first_document = _make_document(session, tmp_path, filename="edp-bill-1.pdf", content_hash="hash-dup-bill-1")

    extracted_bill = ExtractedBill(
        provider="EDP", category_hint="electricity", amount=87.32, currency="EUR",
        due_date=date(2026, 8, 5), paid_date=None, statement_period="2026-07",
    )

    async def fake_extract_bill(file_path, client=None):
        return extracted_bill

    async def fake_extract_utility_detail(file_path, utility_type, client=None):
        return ExtractedUtilityDetail(
            period_label="2026-07", billing_period_start=None, billing_period_end=None,
            invoice_number=None, consumption_value=None, consumption_unit=None,
            energy_cost=None, power_cost=None, fees_taxes_cost=None, vat_cost=None,
        )

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return None

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline, "extract_utility_detail", fake_extract_utility_detail)
    monkeypatch.setattr(pipeline, "assess_and_update_wiki", fake_assess_and_update_wiki)

    first_result = await pipeline.ingest_document(session, first_document)
    assert first_result.status == DocumentStatus.PROCESSED
    assert first_result.failure_reason is None

    second_document = _make_document(session, tmp_path, filename="edp-bill-2.pdf", content_hash="hash-dup-bill-2")
    second_result = await pipeline.ingest_document(session, second_document)

    assert second_result.status == DocumentStatus.PROCESSED
    assert second_result.failure_reason is not None
    assert "duplicate" in second_result.failure_reason
    second_transaction = session.exec(
        select(Transaction).where(Transaction.document_id == second_document.id)
    ).first()
    assert second_transaction is None


@pytest.mark.asyncio
async def test_ingest_document_bill_dedup_still_works_when_prior_document_doc_type_is_null(
    session, monkeypatch, tmp_path
):
    """Pre-migration Documents have doc_type=None (the column didn't exist
    before this branch, and every such document was necessarily a bill).
    The NULL-safe or_() filter must still treat these as bill-derived and
    participate in dedup, not silently exclude them."""
    legacy_document = _make_document(session, tmp_path, filename="legacy-bill.pdf", content_hash="hash-legacy")
    legacy_document.status = DocumentStatus.PROCESSED
    assert legacy_document.doc_type is None
    session.add(legacy_document)
    session.commit()

    session.add(Transaction(
        document_id=legacy_document.id, provider="EDP", category=Category.ELECTRICITY,
        transaction_type=TransactionType.DEBIT, amount=87.32, currency="EUR", statement_period="2026-07",
    ))
    session.commit()

    new_document = _make_document(session, tmp_path, filename="new-edp-bill.pdf", content_hash="hash-new-vs-legacy")

    extracted_bill = ExtractedBill(
        provider="EDP", category_hint="electricity", amount=87.32, currency="EUR",
        due_date=None, paid_date=None, statement_period="2026-07",
    )

    async def fake_extract_bill(file_path, client=None):
        return extracted_bill

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline, "extract_bill", fake_extract_bill)

    result = await pipeline.ingest_document(session, new_document)

    assert result.status == DocumentStatus.PROCESSED
    assert result.failure_reason is not None
    assert "duplicate" in result.failure_reason
    new_transaction = session.exec(
        select(Transaction).where(Transaction.document_id == new_document.id)
    ).first()
    assert new_transaction is None


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
async def test_ingest_bill_creates_utility_reading_for_water_category(session, monkeypatch, tmp_path):
    document = _make_document(session, tmp_path, filename="water-bill.pdf", content_hash="hash-util-water")

    extracted = ExtractedBill(
        provider="EPAL", category_hint="water", amount=20.0, currency="EUR",
        due_date=None, paid_date=None, statement_period="2026-07",
    )

    async def fake_extract_bill(file_path, client=None):
        return extracted

    async def fake_extract_utility_detail(file_path, utility_type, client=None):
        return ExtractedUtilityDetail(
            period_label="2026-07", billing_period_start=None, billing_period_end=None,
            invoice_number="INV-001", consumption_value=12.0, consumption_unit="m3",
            energy_cost=None, power_cost=None, fees_taxes_cost=None, vat_cost=None,
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
    assert reading.utility_type == UtilityType.WATER
    assert reading.consumption_unit == "m3"


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
    assert result.failure_reason is not None
    assert "utility model call failed" in result.failure_reason
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

    calls = []

    async def spy_extract_utility_detail(file_path, utility_type, client=None):
        calls.append((file_path, utility_type))
        raise RuntimeError("should never be called for non-utility categories")

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return None

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline, "extract_utility_detail", spy_extract_utility_detail)
    monkeypatch.setattr(pipeline, "assess_and_update_wiki", fake_assess_and_update_wiki)

    result = await pipeline.ingest_document(session, document)

    assert calls == []
    assert result.status == DocumentStatus.PROCESSED
    assert session.exec(
        select(UtilityReading).where(UtilityReading.document_id == document.id)
    ).first() is None


@pytest.mark.asyncio
async def test_ingest_bill_calls_classify_transaction(session, monkeypatch, tmp_path):
    document = _make_document(session, tmp_path, filename="classify-bill.pdf", content_hash="hash-pipeline-classify-1")

    extracted = ExtractedBill(
        provider="EDP", category_hint="electricity", amount=50.0, currency="EUR",
        due_date=None, paid_date=None, statement_period="2026-08",
    )

    calls = []

    async def spy_classify_transaction(session, transaction, client=None):
        calls.append(transaction.id)

    async def fake_extract_bill(file_path, client=None):
        return extracted

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return None

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline, "assess_and_update_wiki", fake_assess_and_update_wiki)
    monkeypatch.setattr(pipeline, "classify_transaction", spy_classify_transaction)

    await pipeline.ingest_document(session, document)

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_ingest_statement_calls_classify_transaction_per_line_item(session, monkeypatch, tmp_path):
    document = _make_document(session, tmp_path, filename="classify-statement.pdf", content_hash="hash-pipeline-classify-2")

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
        ],
    )

    calls = []

    async def spy_classify_transaction(session, transaction, client=None):
        calls.append(transaction.id)

    async def fake_extract_statement_transactions(file_path, client=None):
        return extracted

    monkeypatch.setattr(pipeline, "classify_document", _fake_classify_statement)
    monkeypatch.setattr(pipeline, "extract_statement_transactions", fake_extract_statement_transactions)
    monkeypatch.setattr(pipeline, "classify_transaction", spy_classify_transaction)

    await pipeline.ingest_document(session, document)

    assert len(calls) == 2
