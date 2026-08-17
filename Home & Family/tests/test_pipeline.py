from datetime import date

import pytest
from sqlmodel import select

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Category, Transaction
from app.services import pipeline
from app.services.extraction import ExtractedBill, ExtractionError


@pytest.mark.asyncio
async def test_ingest_document_creates_transaction(session, monkeypatch, tmp_path):
    document = Document(
        filename="bill.pdf", file_path=str(tmp_path / "bill.pdf"), content_hash="hash1",
        source=DocumentSource.MANUAL, status=DocumentStatus.PENDING,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    extracted = ExtractedBill(
        provider="EDP", category_hint="electricity", amount=87.32, currency="EUR",
        due_date=date(2026, 9, 5), paid_date=None, statement_period="2026-08",
    )

    async def fake_extract_bill(image_path, client=None):
        return extracted

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return None

    monkeypatch.setattr(pipeline, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline, "ensure_image", lambda path: path)
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


@pytest.mark.asyncio
async def test_ingest_document_marks_needs_attention_on_extraction_failure(session, monkeypatch, tmp_path):
    document = Document(
        filename="bad.pdf", file_path=str(tmp_path / "bad.pdf"), content_hash="hash2",
        source=DocumentSource.MANUAL, status=DocumentStatus.PENDING,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    async def failing_extract_bill(image_path, client=None):
        raise ExtractionError("could not parse")

    monkeypatch.setattr(pipeline, "extract_bill", failing_extract_bill)
    monkeypatch.setattr(pipeline, "ensure_image", lambda path: path)

    result = await pipeline.ingest_document(session, document)

    assert result.status == DocumentStatus.NEEDS_ATTENTION
    assert "could not parse" in result.failure_reason


@pytest.mark.asyncio
async def test_ingest_document_skips_duplicate_transaction(session, monkeypatch, tmp_path):
    existing_document = Document(
        filename="first.pdf", file_path=str(tmp_path / "first.pdf"), content_hash="hash-a",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(existing_document)
    session.commit()
    session.refresh(existing_document)

    session.add(Transaction(
        document_id=existing_document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=87.32, currency="EUR", statement_period="2026-08",
    ))
    session.commit()

    new_document = Document(
        filename="duplicate.pdf", file_path=str(tmp_path / "duplicate.pdf"), content_hash="hash-b",
        source=DocumentSource.MANUAL, status=DocumentStatus.PENDING,
    )
    session.add(new_document)
    session.commit()
    session.refresh(new_document)

    extracted = ExtractedBill(
        provider="EDP", category_hint="electricity", amount=87.32, currency="EUR",
        due_date=None, paid_date=None, statement_period="2026-08",
    )

    async def fake_extract_bill(image_path, client=None):
        return extracted

    monkeypatch.setattr(pipeline, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline, "ensure_image", lambda path: path)

    result = await pipeline.ingest_document(session, new_document)

    assert result.status == DocumentStatus.PROCESSED
    assert "duplicate" in result.failure_reason
    transactions = session.exec(
        select(Transaction).where(Transaction.document_id == new_document.id)
    ).all()
    assert transactions == []
