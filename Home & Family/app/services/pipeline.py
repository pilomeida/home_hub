"""Orchestrates the document ingestion pipeline: extract -> categorize ->
dedup -> persist -> todo -> wiki. Every ingestion channel (manual upload,
email, bank sync) calls ingest_document as its single entry point."""

from __future__ import annotations

from sqlmodel import Session

from app.models.document import Document, DocumentStatus
from app.models.transaction import Transaction
from app.services.categorization import normalize_category
from app.services.dedup import find_duplicate_transaction
from app.services.extraction import ExtractedBill, ensure_image, extract_bill
from app.services.todo_engine import generate_todo_for_transaction
from app.services.wiki_engine import assess_and_update_wiki


async def ingest_document(session: Session, document: Document) -> Document:
    """Run the full ingestion pipeline for a Document already saved to disk.
    Updates and persists the Document's status before returning it."""
    try:
        image_path = ensure_image(document.file_path)
        extracted: ExtractedBill = await extract_bill(image_path)
    except Exception as exc:
        # Broad by design: ingest_document is the ingestion boundary — any
        # extraction-side failure (SDK errors from extract_bill's Anthropic
        # call, PDF-specific errors from ensure_image's pdf2image call, or
        # anything else unanticipated) must land the Document on
        # needs_attention with a reason, never propagate uncaught and leave
        # it stuck at PENDING.
        document.status = DocumentStatus.NEEDS_ATTENTION
        document.failure_reason = str(exc)
        session.add(document)
        session.commit()
        session.refresh(document)
        return document

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
        document.status = DocumentStatus.NEEDS_ATTENTION
        document.failure_reason = f"processed but enrichment failed: {exc}"
        session.add(document)
        session.commit()
        session.refresh(document)
        return document

    document.status = DocumentStatus.PROCESSED
    document.failure_reason = None
    session.add(document)
    session.commit()
    session.refresh(document)
    return document
