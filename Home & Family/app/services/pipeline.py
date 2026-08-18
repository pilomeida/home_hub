"""Orchestrates the document ingestion pipeline: classify -> extract ->
categorize -> dedup -> persist -> todo -> wiki. Every ingestion channel
(manual upload, email, bank sync) calls ingest_document as its single entry
point."""

from __future__ import annotations

from sqlmodel import Session

from app.models.document import Document, DocumentStatus
from app.models.transaction import Transaction, TransactionType
from app.models.utility_reading import UtilityReading, UtilityType
from app.services.categorization import normalize_category
from app.services.dedup import find_duplicate_transaction
from app.services.extraction import (
    ExtractedBill,
    classify_document,
    extract_bill,
    extract_statement_transactions,
    extract_utility_detail,
)
from app.services.todo_engine import generate_todo_for_transaction
from app.services.wiki_engine import assess_and_update_wiki

_UTILITY_CATEGORY_VALUES = {"electricity", "water", "telecom"}


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

    document.doc_type = doc_type

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
                transaction_type=TransactionType(item.transaction_type.strip().lower()),
                amount=abs(item.amount),
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
