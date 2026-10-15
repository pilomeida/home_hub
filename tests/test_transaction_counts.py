"""Transaction.counts(): the single definition of which rows count in totals (decision D17)."""
from datetime import date

import pytest
from sqlmodel import select

from app.models.document import Document, DocumentSource
from app.models.transaction import Transaction


def _tx(session, doc, **kw):
    t = Transaction(document_id=doc.id, description="x", amount=10.0, paid_date=date(2026, 3, 1), **kw)
    session.add(t); session.commit(); session.refresh(t)
    return t


@pytest.fixture()
def doc(session):
    d = Document(filename="s.pdf", file_path="s", content_hash="s", source=DocumentSource.MANUAL)
    session.add(d); session.commit(); session.refresh(d)
    return d


def test_only_rows_that_count_are_selected(session, doc):
    ok = _tx(session, doc)
    confirmed = _tx(session, doc, review_status="confirmed")
    held = _tx(session, doc, review_status="needs_check", review_reason="smudged")
    rejected = _tx(session, doc, review_status="rejected")
    settled = _tx(session, doc, settled_by_id=ok.id)
    counted = {t.id for t in session.exec(select(Transaction).where(Transaction.counts()))}
    assert counted == {ok.id, confirmed.id}
    assert not {held.id, rejected.id, settled.id} & counted


def test_a_row_that_is_both_settled_and_confirmed_still_does_not_count(session, doc):
    ok = _tx(session, doc)
    both = _tx(session, doc, review_status="confirmed", settled_by_id=ok.id)
    assert both.id not in {t.id for t in session.exec(select(Transaction).where(Transaction.counts()))}
