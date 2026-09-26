import io

import app.domains.financials.handler as financials_handler
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Category, Transaction, TransactionType


async def _fake_process(session, document):
    document.status = DocumentStatus.PROCESSED
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def test_upload_bill_creates_document_and_redirects(client, monkeypatch):
    monkeypatch.setattr(financials_handler, "process_financials_document", _fake_process)

    response = client.post(
        "/financials/bills/upload",
        files={"file": ("bill.pdf", io.BytesIO(b"fake-pdf-bytes"), "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/financials/bills/")


def test_list_bills_renders(client):
    response = client.get("/financials/bills")
    assert response.status_code == 200
    assert "Bills" in response.text


def test_bill_detail_404_for_missing_document(client):
    response = client.get("/financials/bills/9999")
    assert response.status_code == 404


def test_bill_detail_renders_multiple_transactions(client, session):
    document = Document(
        filename="statement.pdf", file_path="/tmp/statement.pdf", content_hash="hstmt",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    session.add(Transaction(
        document_id=document.id, provider="CONTINENTE", category=Category.GROCERIES,
        transaction_type=TransactionType.DEBIT, amount=40.0, currency="EUR",
        statement_period="2026-08",
    ))
    session.add(Transaction(
        document_id=document.id, provider="SALARIO", category=Category.INCOME,
        transaction_type=TransactionType.CREDIT, amount=2000.0, currency="EUR",
        statement_period="2026-08",
    ))
    session.commit()

    response = client.get(f"/financials/bills/{document.id}")

    assert response.status_code == 200
    assert "CONTINENTE" in response.text
    assert "SALARIO" in response.text


def test_upload_bill_sets_financials_domain_and_account(client, monkeypatch, session):
    from app.models.account import Account, AccountType
    from app.models.domain import Domain

    account = Account(name="Santander", institution="Santander Totta", account_type=AccountType.CHECKING)
    session.add(account)
    session.commit()
    session.refresh(account)
    monkeypatch.setattr(financials_handler, "process_financials_document", _fake_process)

    response = client.post(
        "/financials/bills/upload",
        data={"account_id": str(account.id)},
        files={"file": ("bill.pdf", io.BytesIO(b"fake-pdf-bytes"), "application/pdf")},
        follow_redirects=False,
    )

    document_id = int(response.headers["location"].rsplit("/", 1)[-1])
    document = session.get(Document, document_id)
    assert document.domain == Domain.FINANCIALS
    assert document.category is None  # the handler infers it; the fake handler did not


def test_bill_detail_shows_a_friendly_message_for_a_raw_failure_reason(client, session):
    document = Document(
        filename="statement.pdf", file_path="/tmp/statement.pdf", content_hash="hbadstmt",
        source=DocumentSource.MANUAL, status=DocumentStatus.NEEDS_ATTENTION,
        failure_reason="'atm_withdrawal' is not a valid TransactionType",
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    response = client.get(f"/financials/bills/{document.id}")

    assert response.status_code == 200
    assert "Some details in this document couldn't be understood." in response.text
    assert "<details>" in response.text and "Technical details" in response.text
    assert "is not a valid TransactionType" in response.text  # kept, inside the technical details


def test_upload_bill_duplicate_redirects_to_existing(client, monkeypatch):
    monkeypatch.setattr(financials_handler, "process_financials_document", _fake_process)
    files = {"file": ("bill.pdf", io.BytesIO(b"same-bytes"), "application/pdf")}
    first = client.post("/financials/bills/upload", files=files, follow_redirects=False)
    second = client.post(
        "/financials/bills/upload",
        files={"file": ("copy.pdf", io.BytesIO(b"same-bytes"), "application/pdf")},
        follow_redirects=False,
    )
    assert second.headers["location"] == first.headers["location"]


def test_list_bills_shows_only_financials_documents(client, session):
    from app.models.domain import Domain

    session.add(Document(filename="edp.pdf", file_path="/tmp/edp.pdf", content_hash="f1",
                         source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED, domain=Domain.FINANCIALS))
    session.add(Document(filename="boiler-manual.pdf", file_path="/tmp/b.pdf", content_hash="h1",
                         source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED, domain=Domain.HOUSE))
    session.commit()

    response = client.get("/financials/bills")

    assert "edp.pdf" in response.text
    assert "boiler-manual.pdf" not in response.text


def test_bills_page_shows_the_financials_backlog(client, session):
    from app.models.domain import Domain
    from app.models.todo import Todo

    session.add(Todo(title="Pay the water bill", domain=Domain.FINANCIALS))
    session.commit()

    response = client.get("/financials/bills")

    assert 'id="backlog-financials"' in response.text
    assert "Pay the water bill" in response.text