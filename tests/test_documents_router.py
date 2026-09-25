import io

import pytest

import app.domains.house.handler as house_handler
from app.domains.house.warranty import WarrantyDates
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    async def no_dates(file_path, client=None):
        return WarrantyDates(None, None)

    monkeypatch.setattr(house_handler, "extract_warranty_dates", no_dates)


def _house_upload(client, data, filename="w.pdf", content=b"w"):
    return client.post("/house/upload", data=data, files={"file": (filename, io.BytesIO(content), "application/pdf")},
                       follow_redirects=False).headers["location"]


def test_edit_screen_offers_domains_categories_and_fields(client, session):
    location = _house_upload(client, {"category": "warranty_invoice", "item_name": "Boiler"})
    document_id = location.rsplit("/", 1)[-1]
    text = client.get(f"/documents/{document_id}/edit").text
    assert "Financials" in text and "House" in text and "Warranty / invoice" in text and 'value="Boiler"' in text
    categories = client.get("/documents/edit/categories?domain=financials").text
    assert "Bill / invoice" in categories and "Warranty" not in categories


def test_refile_house_document_to_another_house_category(client, session):
    location = _house_upload(client, {"category": "warranty_invoice", "item_name": "Boiler"})
    document_id = location.rsplit("/", 1)[-1]

    response = client.post(f"/documents/{document_id}/edit", data={
        "domain": "house", "category": "house_appliance", "item_name": "Boiler", "room": "Kitchen",
    }, follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == f"/house/documents/{document_id}"
    assert session.get(Document, int(document_id)).category == "house_appliance"


def test_refile_into_maintenance_makes_the_file_a_record_attachment(client, session):
    location = _house_upload(client, {"category": "house_appliance", "item_name": "Boiler"})
    document_id = location.rsplit("/", 1)[-1]

    response = client.post(f"/documents/{document_id}/edit", data={
        "domain": "house", "category": "maintenance_log", "item_name": "Boiler", "service_date": "2026-02-02",
    }, follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"].startswith("/house/records/")
    assert client.get(location, follow_redirects=False).headers["location"] == response.headers["location"]


def test_edit_validation_errors_and_unfinalized_404(client, session):
    location = _house_upload(client, {"category": "warranty_invoice", "item_name": "Boiler"})
    document_id = location.rsplit("/", 1)[-1]
    bad = client.post(f"/documents/{document_id}/edit", data={"domain": "house", "category": "floor_plan"})
    assert bad.status_code == 400 and "System is required" in bad.text

    pending = Document(filename="p.pdf", file_path="/tmp/p.pdf", content_hash="pp", source=DocumentSource.MANUAL)
    session.add(pending)
    session.commit()
    session.refresh(pending)
    assert client.get(f"/documents/{pending.id}/edit").status_code == 404
