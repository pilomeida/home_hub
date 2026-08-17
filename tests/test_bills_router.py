import io

import app.routers.bills as bills_router
from app.models.document import DocumentStatus


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
