from app.models.document import Document, DocumentSource, DocumentStatus


def test_create_and_read_document(session):
    document = Document(
        filename="edp-august.pdf",
        file_path="/data/documents/edp-august.pdf",
        content_hash="abc123",
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    assert document.id is not None
    assert document.status == DocumentStatus.PENDING
    assert document.password_protected is False


def test_document_account_id_defaults_to_none(session):
    document = Document(
        filename="statement.pdf", file_path="/tmp/statement.pdf",
        content_hash="hash-account-test", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    assert document.account_id is None


def test_document_account_id_can_be_set(session):
    from app.models.account import Account, AccountType

    account = Account(name="Current Account", institution="Millennium BCP", currency="EUR", account_type=AccountType.CHECKING)
    session.add(account)
    session.commit()
    session.refresh(account)

    document = Document(
        filename="statement.pdf", file_path="/tmp/statement2.pdf",
        content_hash="hash-account-test-2", source=DocumentSource.MANUAL,
        account_id=account.id,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    fetched = session.get(Document, document.id)
    assert fetched.account_id == account.id
