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


def test_document_domain_defaults_to_none(session):
    document = Document(
        filename="s.pdf", file_path="/tmp/s.pdf", content_hash="hash-domain-default",
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    assert document.domain is None


def test_document_domain_can_be_set(session):
    from app.models.domain import Domain

    document = Document(
        filename="s.pdf", file_path="/tmp/s.pdf", content_hash="hash-domain-set",
        source=DocumentSource.MANUAL, domain=Domain.FINANCIALS,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    assert document.domain == Domain.FINANCIALS


def test_document_category_and_fields_default(session):
    document = Document(
        filename="manual.pdf", file_path="/tmp/manual.pdf",
        content_hash="hash-category-default", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    assert document.category is None
    assert document.fields_json == "{}"


def test_document_accepts_house_domain_category_and_fields(session):
    from app.models.domain import Domain

    document = Document(
        filename="boiler-warranty.pdf", file_path="/tmp/boiler-warranty.pdf",
        content_hash="hash-house", source=DocumentSource.MANUAL,
        domain=Domain.HOUSE, category="warranty_invoice",
        fields_json='{"item_name": "Boiler"}',
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    assert document.domain == Domain.HOUSE
    assert document.category == "warranty_invoice"
    assert document.fields_json == '{"item_name": "Boiler"}'
