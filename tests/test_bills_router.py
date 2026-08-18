import io

import app.routers.bills as bills_router
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Category, Transaction, TransactionType


def test_upload_bill_creates_document_and_redirects(client, monkeypatch):
    async def fake_ingest_document(session, document):
        document.status = DocumentStatus.PROCESSED
        return document

    monkeypatch.setattr(bills_router, "ingest_document", fake_ingest_document)

    response = client.post(
        "/bills/upload",
        files={"file": ("bill.pdf", io.BytesIO(b"fake-pdf-bytes"), "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/bills/")


def test_list_bills_renders(client):
    response = client.get("/bills")
    assert response.status_code == 200
    assert "Bills" in response.text


def test_bill_detail_404_for_missing_document(client):
    response = client.get("/bills/9999")
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

    response = client.get(f"/bills/{document.id}")

    assert response.status_code == 200
    assert "CONTINENTE" in response.text
    assert "SALARIO" in response.text
