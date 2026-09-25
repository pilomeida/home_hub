"""Financials domain handler: the bill/statement pipeline (classify ->
extract -> categorize -> dedup -> persist -> todo -> wiki). Reached only
through the generic ingestion core (app.services.ingestion ->
FinancialsHandler.process); routers, scripts and channels never call it
directly."""

from __future__ import annotations

from sqlmodel import Session

from app.domains.base import DomainHandler
from app.domains.fields import dump_fields, load_fields
from app.domains.financials.categories import FinancialsCategory
from app.models.document import Document, DocumentStatus
from app.models.merchant import Merchant
from app.models.transaction import Transaction, TransactionType
from app.models.utility_reading import UtilityReading, UtilityType
from app.services.categorization import normalize_category
from app.services.classification_engine import classify_transaction
from app.services.dedup import find_duplicate_transaction
from app.services.extraction import (
    ExtractedBill,
    classify_document,
    extract_bill,
    extract_statement_transactions,
    extract_utility_detail,
)
from app.services.ingestion import mark_needs_attention
from app.services.todo_engine import generate_todo_for_transaction
from app.services.wiki_engine import ingest_into_wiki

_UTILITY_CATEGORY_VALUES = {t.value for t in UtilityType}


def _wiki_context(transaction: Transaction) -> str:
    # Text-only context (no file) -- the same cheap input the pre-knowledge-
    # layer wiki assessment used.
    return (
        f"Provider: {transaction.provider}\n"
        f"Category: {transaction.category.value}\n"
        f"Statement period: {transaction.statement_period}\n"
    )


async def process_financials_document(session: Session, document: Document) -> Document:
    """Run the Financials pipeline for a Document the ingestion core has
    already finalized (domain, category -- possibly None -- and fields set).
    Updates and persists the Document's status before returning it."""
    fields = load_fields(document)
    account_id = fields.pop("account_id", None)
    if account_id is not None:
        # account_id is declared as a FieldSpec so every channel's form
        # (upload, Plan B's Inbox) collects it generically, but it lives in
        # the real Document.account_id FK column -- moved there, not duplicated.
        document.account_id = int(account_id)
        document.fields_json = dump_fields(fields)

    if document.category is None:
        try:
            document.category = await classify_document(document.file_path)
        except Exception as exc:
            # Broad by design: any classification failure must land the
            # Document on needs_attention with a reason, never propagate.
            return mark_needs_attention(session, document, str(exc))

    if document.category == FinancialsCategory.STATEMENT.value:
        return await _ingest_statement(session, document)
    return await _ingest_bill(session, document)


class FinancialsHandler(DomainHandler):
    async def process(self, session: Session, document: Document) -> Document:
        # Module-level lookup at call time, so tests can monkeypatch
        # process_financials_document.
        return await process_financials_document(session, document)


async def _log_ingest_without_assessment(session: Session, document: Document) -> None:
    # Every processed document is logged as ingested into the wiki (Plan C's
    # lint relies on it). No context => no LLM call; Financials declares no
    # entity types, so this only writes the log entry.
    try:
        await ingest_into_wiki(session, document)
    except Exception as exc:
        session.rollback()
        print(f"wiki ingest log failed for document {document.id}: {exc}")


async def _ingest_bill(session: Session, document: Document) -> Document:
    try:
        extracted: ExtractedBill = await extract_bill(document.file_path)
    except Exception as exc:
        # Broad by design — see process_financials_document: any extraction
        # failure (Anthropic SDK errors, malformed responses, anything
        # unanticipated) must land on needs_attention, never propagate
        # uncaught and leave the Document stuck at PENDING.
        return mark_needs_attention(session, document, str(exc))

    duplicate = find_duplicate_transaction(
        session, provider=extracted.provider, statement_period=extracted.statement_period
    )
    if duplicate is not None:
        await _log_ingest_without_assessment(session, document)
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
        await classify_transaction(session, transaction)
        session.commit()
    except Exception as exc:
        # Classification is enrichment on top of an already-successful bill,
        # matching how utility-detail extraction below already degrades —
        # a classification failure must not affect the bill's own
        # Transaction/Document outcome.
        session.rollback()
        print(f"classification failed for transaction {transaction.id}: {exc}")

    utility_detail_failure_reason = None
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
            utility_detail_failure_reason = f"utility detail extraction failed: {exc}"

    try:
        generate_todo_for_transaction(session, transaction)
        await ingest_into_wiki(session, document, context=_wiki_context(transaction))
    except Exception as exc:
        # The Transaction is already safely committed at this point — an
        # enrichment failure (todo generation or the wiki's Claude call /
        # response parsing) must not leave Document.status stuck at PENDING,
        # an inconsistent partial-success state invisible to the dashboard.
        return mark_needs_attention(session, document, f"processed but enrichment failed: {exc}")

    document.status = DocumentStatus.PROCESSED
    document.failure_reason = utility_detail_failure_reason
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


async def _ingest_statement(session: Session, document: Document) -> Document:
    # Transactions (and any brand-new Merchants) committed so far in this
    # attempt — tracked so that if a later line item fails (e.g. a bad
    # transaction_type value), we can explicitly delete them below. A plain
    # session.rollback() only discards *uncommitted* state; it can no longer
    # undo an earlier line item's Transaction (or Merchant) row once we've
    # committed per item. Note: classify_transaction itself never reads
    # transaction.id, so a session.flush() per item (assigning an id without
    # committing) would likely have sufficed instead of a real commit — but
    # this working, tested per-item-commit-plus-compensating-delete design
    # was kept as-is deliberately rather than risk a refactor here.
    created_transactions: list[Transaction] = []
    created_merchant_ids: list[int] = []
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
            session.refresh(transaction)
            created_transactions.append(transaction)
            try:
                result = await classify_transaction(session, transaction)
                if result.created_new_merchant:
                    created_merchant_ids.append(result.merchant.id)
                session.commit()
            except Exception as exc:
                # Same reasoning as _ingest_bill: classification failure
                # must not affect the statement's own ingestion outcome.
                session.rollback()
                print(f"classification failed for transaction {transaction.id}: {exc}")
    except Exception as exc:
        # Roll back any staged-but-uncommitted state, then explicitly
        # delete any line items (and any brand-new Merchants they created)
        # from this attempt that were already committed above, before
        # marking needs_attention — so nothing partially-ingested leaks
        # into the next commit (matching the pre-per-item-commit behavior,
        # where a single trailing commit made a plain rollback sufficient).
        # A Merchant is only ever deleted here if created_new_merchant was
        # True for it, which means it did not exist before this exact
        # attempt — so nothing outside this failed attempt could already be
        # referencing it, and every Transaction created during this same
        # attempt is being deleted right alongside it.
        session.rollback()
        for transaction in created_transactions:
            db_transaction = session.get(Transaction, transaction.id)
            if db_transaction is not None:
                session.delete(db_transaction)
        for merchant_id in created_merchant_ids:
            db_merchant = session.get(Merchant, merchant_id)
            if db_merchant is not None:
                session.delete(db_merchant)
        session.commit()
        return mark_needs_attention(session, document, str(exc))

    # Statement-derived transactions are historical and already settled —
    # unlike bills, they never generate a to-do (no upcoming due date) and
    # get no LLM wiki assessment; they are only logged as ingested. Duplicate
    # line items
    # sharing a provider/period within one statement are expected, not a
    # dedup signal; only whole-file re-upload (content-hash dedup, already
    # enforced by the ingestion core's receive_file) applies here.
    await _log_ingest_without_assessment(session, document)
    document.status = DocumentStatus.PROCESSED
    document.failure_reason = None
    session.add(document)
    session.commit()
    session.refresh(document)
    return document
