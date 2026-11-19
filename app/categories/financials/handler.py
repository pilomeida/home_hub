"""Financials domain handler: the bill/statement pipeline (classify ->
extract -> categorize -> dedup -> persist -> todo -> wiki). Reached only
through the generic ingestion core (app.services.ingestion ->
FinancialsHandler.process); routers, scripts and channels never call it
directly."""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session, or_, select

from app.categories.base import CategoryHandler, read_by_kind
from app.categories.fields import dump_fields, load_fields
from app.categories.financials.feed import is_feed_statement
from app.models.category import Category
from app.models.document import Document, DocumentStatus
from app.models.category_node import CategoryNode
from app.models.merchant import Merchant
from app.models.position import PositionExtraction
from app.models.todo import Todo
from app.models.transaction import StatementCheck, Transaction, TransactionType
from app.models.utility_reading import UtilityReading, UtilityType
from app.services.debt_ledger import detach_entries_for_transactions
from app.services.classification_engine import classify_transaction
from app.services.dedup import find_duplicate_transaction
from app.services.document_reconcile import reconcile_quietly
from app.services.extraction import (
    ClassificationError,
    ExtractedBill,
    extract_bill,
    extract_statement_transactions,
    extract_utility_detail,
)
from app.services.ingestion import mark_needs_attention
from app.services.statement_check import apply_statement_check
from app.services.position_store import (
    detach_positions, process_loan_history_document, process_positions_document,
)
from app.services.taxonomy import path_label
from app.services.todo_engine import generate_todo_for_transaction
from app.services.wiki_engine import ingest_into_wiki

# The tree leaves whose bills carry a meter reading (work-list B3: read from the tree, not the old flat list).
_UTILITY_LEAVES = {f"housing.utilities.{t.value}": t for t in UtilityType}


def _node(session: Session, transaction: Transaction) -> Optional[CategoryNode]:
    return session.get(CategoryNode, transaction.category_id) if transaction.category_id else None


def _utility_type(session: Session, transaction: Transaction, hint: str) -> Optional[UtilityType]:
    """The bill's utility, from where it is filed on the tree. A bill still in Unsorted falls back on the
    reader's own word for it (electricity, water, telecom)."""
    node = _node(session, transaction)
    if node is not None and not node.slug.startswith("unsorted"):
        return _UTILITY_LEAVES.get(node.slug)
    word = (hint or "").strip().lower()
    return next((t for t in UtilityType if t.value == word), None)


def _wiki_context(session: Session, transaction: Transaction) -> str:
    # Text-only context (no file) -- the same cheap input the pre-knowledge-
    # layer wiki assessment used.
    node = _node(session, transaction)
    return (
        f"Provider: {transaction.description}\n"
        f"Category: {path_label(session, node) if node is not None else 'not filed yet'}\n"
        f"Statement period: {transaction.statement_period}\n"
    )


async def classify_document(file_path: str, gateway=None) -> str:
    """A hand upload's kind, decided once and the same way the Inbox decides it (PLAN step 2d, C3): the Inbox's own
    classifier, limited to Money & Bills. (The separate bill-or-statement prompt that ran after approval is gone.)"""
    from app.categories.registry import get_spec
    from app.services.category_classifier import suggest_category_and_kind
    suggestion = await suggest_category_and_kind(file_path, categories=[get_spec(Category.FINANCIALS)], gateway=gateway)
    if suggestion.category != Category.FINANCIALS or not suggestion.paper_kind:
        raise ClassificationError(f"Could not tell what kind of money paper this is: {suggestion.note or 'no answer'}")
    return suggestion.paper_kind


def _recognised_kind(session: Session, document: Document) -> Optional[str]:
    """A person's recognition rule (sender, words, file name), with no AI: the Inbox's first step too."""
    from pathlib import Path
    from app.privacy.recognise import recognise
    try:
        content = Path(document.file_path).read_bytes()
    except OSError:
        return None
    found = recognise(session, filename=document.filename, content=content, context_text=None)
    return found.paper_kind if found is not None and found.category == Category.FINANCIALS.value else None


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

    if document.paper_kind is None:
        try:
            document.paper_kind = _recognised_kind(session, document) or await classify_document(document.file_path)
        except Exception as exc:
            # Broad by design: any classification failure must land the
            # Document on needs_attention with a reason, never propagate.
            return mark_needs_attention(session, document, str(exc))

    return await read_by_kind(session, document, Category.FINANCIALS, default=read_bill)     # each kind declares its reader (spec.py)


class FinancialsHandler(CategoryHandler):
    async def process(self, session: Session, document: Document) -> Document:
        # Module-level lookup at call time, so tests can monkeypatch
        # process_financials_document.
        return await process_financials_document(session, document)

    def refile_blocker(self, session: Session, document: Document) -> Optional[str]:
        if is_feed_statement(document):
            return ("This is the bank feed's statement, not a paper: its lines came from the bank, so it stays where "
                    "it is. To change one line, open it in Transactions.")
        # keeps held rows: re-file safety check must see every row of the document, held or not
        transactions = session.exec(select(Transaction).where(Transaction.document_id == document.id)).all()
        ids = [t.id for t in transactions]
        linked = [t for t in transactions if t.commitment_id or t.debt_id or t.linked_transaction_id]
        if ids and not linked:
            # keeps held rows: re-file safety check must see every linked row, held or not
            linked = session.exec(select(Transaction).where(Transaction.linked_transaction_id.in_(ids))).all()
        if linked:
            return (
                f"{len(linked)} transaction(s) from this document are linked to a commitment, debt or transfer. "
                "Remove those links in Transactions first — re-filing would discard them."
            )
        return None

    async def withdraw(self, session: Session, document: Document) -> None:
        """Reverse Financials' automated derivations for this document.
        Merchants are shared across documents and are kept; loan/savings
        position rows are detached, never deleted."""
        # keeps held rows: deleting a document removes ALL its rows, held ones too
        transactions = session.exec(select(Transaction).where(Transaction.document_id == document.id)).all()
        ids = [t.id for t in transactions]
        for reading in session.exec(select(UtilityReading).where(UtilityReading.document_id == document.id)).all():
            session.delete(reading)
        for check in session.exec(select(StatementCheck).where(StatementCheck.document_id == document.id)).all():
            session.delete(check)
        if ids:
            for todo in session.exec(select(Todo).where(Todo.transaction_id.in_(ids))).all():
                if todo.done:
                    todo.transaction_id = None
                    todo.document_id = document.id
                    session.add(todo)
                else:
                    session.delete(todo)
            # A bill settled by one of these bank rows becomes the open record of its payment again.
            # keeps held rows: releasing bills this document's bank rows settled; a held bank row still settled them
            for settled in session.exec(select(Transaction).where(Transaction.settled_by_id.in_(ids))).all():
                settled.settled_by_id = None
                session.add(settled)
            # Informal-loan ledger entries made from these transactions are kept.
            detach_entries_for_transactions(session, ids)
            session.flush()
            for transaction in transactions:
                session.delete(transaction)
        # Registered data is never discarded: loan/savings rows read from this
        # document keep living, detached (document_id NULL); only the extraction
        # bookkeeping row goes. Re-uploading the file re-attaches them.
        detach_positions(session, document.id)
        for row in session.exec(select(PositionExtraction).where(PositionExtraction.document_id == document.id)).all():
            session.delete(row)
        document.account_id = None
        session.add(document)
        session.commit()


async def _log_ingest_without_assessment(session: Session, document: Document) -> None:
    # Every processed document is logged as ingested into the wiki (Plan C's
    # lint relies on it). No context => no LLM call; Financials declares no
    # entity types, so this only writes the log entry.
    try:
        await ingest_into_wiki(session, document)
    except Exception as exc:
        session.rollback()
        print(f"wiki ingest log failed for document {document.id}: {exc}")


async def read_bill(session: Session, document: Document) -> Document:
    try:
        extracted: ExtractedBill = await extract_bill(document.file_path)
    except Exception as exc:
        # Broad by design — see process_financials_document: any extraction
        # failure (Anthropic SDK errors, malformed responses, anything
        # unanticipated) must land on needs_attention, never propagate
        # uncaught and leave the Document stuck at PENDING.
        return mark_needs_attention(session, document, str(exc))

    duplicate = find_duplicate_transaction(
        session, description=extracted.provider, statement_period=extracted.statement_period,
        amount=extracted.amount, issue_date=extracted.issue_date,
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
        description=extracted.provider,
        transaction_type=TransactionType.DEBIT,
        amount=extracted.amount,
        currency=extracted.currency,
        issue_date=extracted.issue_date,
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
    utility = _utility_type(session, transaction, extracted.category_hint)
    if utility is not None:
        try:
            detail = await extract_utility_detail(document.file_path, utility.value)
            reading = UtilityReading(
                document_id=document.id,
                utility_type=utility,
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
        await ingest_into_wiki(session, document, context=_wiki_context(session, transaction))
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
    reconcile_quietly(session)  # the bank may already show this bill's payment
    session.refresh(document)
    return document


async def read_statement_positions(session: Session, document: Document) -> Document:
    """A consolidated statement read only for its loan, fund and balance positions."""
    return await _read_positions(session, document, process_positions_document)


async def read_loan_history(session: Session, document: Document) -> Document:
    """An online-banking printout of one loan's instalment movements."""
    return await _read_positions(session, document, process_loan_history_document)


async def _read_positions(session: Session, document: Document, processor) -> Document:
    """Loans/savings documents: never transaction extraction. A failed
    extraction is already marked needs_attention by the processor; success
    (including a history awaiting a loan assignment) ends PROCESSED."""
    try:
        extraction = await processor(session, document)
    except Exception as exc:
        session.rollback()
        return mark_needs_attention(session, document, str(exc))
    if extraction.status == "failed":
        return document
    await _log_ingest_without_assessment(session, document)
    document.status = DocumentStatus.PROCESSED
    document.failure_reason = None
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


_MONEY_IN = ("credit", "deposit", "refund", "income", "salary", "received", "receipt", "interest_earned", "cashback",
             "reimburse", "inflow", "incoming")


def line_type(raw: str) -> TransactionType:
    """A statement line's direction. The reader sometimes names it in its own words ('atm_withdrawal', 'insurance':
    documents 22 and 33 were refused whole for it); those are mapped from the words instead of failing the statement
    (PLAN step 4c). The statement's own add-up check (decision D17) still catches a wrong guess."""
    word = (raw or "").strip().lower()
    try:
        return TransactionType(word)
    except ValueError:
        pass
    if "transfer" in word:
        return TransactionType.TRANSFER
    return TransactionType.CREDIT if any(w in word for w in _MONEY_IN) else TransactionType.DEBIT


CARD_NO_ACCOUNT = ("the Hub doesn't know which card this statement belongs to. Choose the card's account in the "
                   "paper's details, then read it again")


async def read_card_statement_document(session: Session, document: Document) -> Document:
    """A credit card's monthly statement, through the same intake as every paper (PLAN step 4b): read from the
    PDF's own text, refused unless its lines add up to its own balances to the cent, and only the lines the card
    does not hold yet are stored (app/services/card_figures.py)."""
    import asyncio
    from app.services import card_figures
    if document.account_id is None:
        return mark_needs_attention(session, document, CARD_NO_ACCOUNT)
    try:
        statement = await asyncio.to_thread(card_figures.read_card_statement, document.file_path)
    except Exception as exc:                  # not a card statement, or it does not add up: stored nothing
        return mark_needs_attention(session, document, f"the card statement couldn't be read: {exc}")
    todo, _redates = card_figures.reconcile(session, document.account_id, statement.lines)
    added = card_figures.add_lines(session, document.account_id, todo, document_id=document.id) if todo else 0
    fields = load_fields(document)
    fields.update({"statement_number": str(statement.number), "issued": statement.issued.isoformat(),
                   "opening_balance": f"{statement.opening:.2f}", "closing_balance": f"{statement.closing:.2f}"})
    document.fields_json = dump_fields(fields)
    document.status, document.failure_reason = DocumentStatus.PROCESSED, None
    session.add(document)
    session.commit()
    from app.models.wiki import WikiOperation
    from app.services.wiki_store import append_log
    append_log(session, WikiOperation.INGEST, f"{document.filename}: card statement {statement.number}, "
               f"{len(statement.lines)} line(s), {added} new, checked to the cent", document_id=document.id)
    reconcile_quietly(session)
    session.refresh(document)
    return document


async def read_statement(session: Session, document: Document) -> Document:
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
                description=item.description,
                transaction_type=line_type(item.transaction_type),
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
                # Same reasoning as read_bill: classification failure
                # must not affect the statement's own ingestion outcome.
                session.rollback()
                print(f"classification failed for transaction {transaction.id}: {exc}")
        # Does the statement add up? Holds doubtful lines and records the check (decision D17). Inside this
        # try so that a failure here undoes the lines like any other failure; a statement that adds up, or
        # holds nothing; one that prints no balances holds only lines the model called unclear.
        apply_statement_check(session, document.id, created_transactions, extracted)
        # Days the bank feed already brought show as overlap, never doubled (PLAN step 4a).
        from app.services.feed_overlap import match_statement_to_feed
        overlap = match_statement_to_feed(session, created_transactions)
        session.commit()
        if overlap.overlapped:
            from app.models.wiki import WikiOperation
            from app.services.wiki_store import append_log
            append_log(session, WikiOperation.INGEST, f"{document.filename}: {overlap.overlapped} line(s) were already "
                       f"in the bank feed; shown as overlap, counted once", document_id=document.id)
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
        # A line item auto-linked to an informal loan keeps its ledger entry (detached).
        detach_entries_for_transactions(session, [t.id for t in created_transactions])
        session.flush()
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
    reconcile_quietly(session)  # bills already filed may now match these bank rows
    session.refresh(document)
    return document
