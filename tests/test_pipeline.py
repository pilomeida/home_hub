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
