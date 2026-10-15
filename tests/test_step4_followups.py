"""Follow-ups from integrating step 4 (decision D17): the pending marker, held rows and bills,
rejecting a line, and linking a held line to a debt. Real models, real routes."""
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app.models.document import Document, DocumentSource
from app.models.domain import Domain
from app.models.transaction import Transaction, TransactionType
from app.services import document_reconcile, to_check


def _doc(session, name, category):
    d = Document(filename=name, file_path=name, content_hash=name, source=DocumentSource.MANUAL,
                 domain=Domain.FINANCIALS, category=category)
    session.add(d); session.commit(); session.refresh(d)
    return d


def _tx(session, doc, **kw):
    base = dict(description="EDP COMERCIAL", amount=40.0, paid_date=date(2026, 3, 10), transaction_type=TransactionType.DEBIT)
    base.update(kw)
    t = Transaction(document_id=doc.id, **base)
    session.add(t); session.commit(); session.refresh(t)
    return t


def test_the_transactions_page_marks_held_and_rejected_rows(client, session):
    d = _doc(session, "s.pdf", "statement")
    _tx(session, d, description="SMUDGED LINE", review_status="needs_check", review_reason="The amount is smudged.")
    _tx(session, d, description="BOGUS LINE", review_status="rejected")
    _tx(session, d, description="NORMAL LINE")
    page = client.get("/financials/transactions").text
    assert "pending check" in page and "not counted yet" in page and "The amount is smudged." in page
    assert "not a real line" in page
    assert page.count("pending check") == 1


@pytest.mark.parametrize("status,settles", [(None, True), ("confirmed", True), ("needs_check", False), ("rejected", False)])
def test_only_bank_rows_that_count_can_settle_a_bill(session, status, settles):
    bill_doc, bank_doc = _doc(session, "bill.pdf", "bill"), _doc(session, "bank.pdf", "statement")
    bill = _tx(session, bill_doc)
    bank = _tx(session, bank_doc, review_status=status)
    document_reconcile.reconcile_documents(session)
    session.refresh(bill)
    assert (bill.settled_by_id == bank.id) is settles


def test_rejecting_a_bank_line_gives_its_settled_bill_back(session):
    bill_doc, bank_doc = _doc(session, "bill.pdf", "bill"), _doc(session, "bank.pdf", "statement")
    bill = _tx(session, bill_doc)
    bank = _tx(session, bank_doc)
    document_reconcile.reconcile_documents(session)
    session.refresh(bill)
    assert bill.settled_by_id == bank.id
    bank.review_status = "needs_check"; session.add(bank); session.commit()      # it is now waiting for a check...
    to_check.reject(session, bank.id, "pedro@example.com")                      # ...and a person says it is not real
    session.refresh(bill)
    assert bill.settled_by_id is None and session.get(Transaction, bank.id).review_status == "rejected"


def test_a_held_line_cannot_be_linked_to_a_debt_and_the_message_says_why(client, session):
    d = _doc(session, "s.pdf", "statement")
    held = _tx(session, d, review_status="needs_check")
    r = client.post(f"/financials/transactions/{held.id}/link-debt", data={"direction": "lent", "person_name": "Ana"})
    assert r.status_code == 409
    text = r.text
    assert "Not linked." in text and "waiting for a check" in text and "To check list" in text
    session.refresh(held)
    assert held.debt_id is None
