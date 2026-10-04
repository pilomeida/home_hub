# tests/test_bank_models.py
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.account import Account
from app.models.bank import BankAccountLink, BankConnection, BankConnectionStatus
from app.models.document import Document, DocumentSource
from app.models.transaction import Transaction


def _doc(session):
    d = Document(filename="sync", file_path="", content_hash="bank-sync:1", source=DocumentSource.API)
    session.add(d); session.commit(); session.refresh(d)
    return d


def test_connection_and_link_roundtrip(session):
    conn = BankConnection(bank_name="Santander Totta", country="PT", state="abc",
                          status=BankConnectionStatus.PENDING)
    session.add(conn); session.commit()
    link = BankAccountLink(connection_id=conn.id, bank_account_uid="u1")
    session.add(link); session.commit()
    assert session.get(BankAccountLink, link.id).last_synced_at is None


def test_same_bank_reference_cannot_be_stored_twice_per_account(session):
    acct = Account(name="Conta", institution="Santander"); session.add(acct); session.commit()
    d = _doc(session)
    kw = dict(document_id=d.id, provider="X", amount=1.0, account_id=acct.id, external_id="ref-1")
    session.add(Transaction(**kw)); session.commit()
    session.add(Transaction(**kw))
    with pytest.raises(IntegrityError):
        session.commit()
