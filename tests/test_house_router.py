import io
import json
from datetime import date

import pytest
from sqlmodel import select

import app.domains.house.handler as house_handler
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.todo import Todo


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    async def fake_extract(file_path, client=None):
        return None

    monkeypatch.setattr(house_handler, "extract_warranty_expiry", fake_extract)


def _upload(client, data, filename="manual.pdf", content=b"pdf-bytes", mime="application/pdf"):
    return client.post("/house/upload", data=data, files={"file": (filename, io.BytesIO(content), mime)},
                       follow_redirects=False)


def test_upload_form_lists_categories_and_prefills(client):
    response = client.get("/house/upload?category=warranty_invoice&item_name=Boiler")
    assert response.status_code == 200
    assert "Warranty / invoice" in response.text and "Floor plan" in response.text
    assert 'value="Boiler"' in response.text


def test_fields_partial_follows_the_category(client):
    floor = client.get("/house/upload/fields?category=floor_plan").text
    assert 'name="system_type"' in floor and 'name="indoor_outdoor"' in floor and 'name="observations"' in floor
    assert 'name="service_date"' not in floor
    assert "<html" not in floor
    assert client.get("/house/upload/fields?category=nope").status_code == 200


def test_field_inputs_id_prefix_keeps_ids_unique(session):
    import re

    from app.domains.fields import build_form_fields
    from app.domains.registry import get_spec
    from app.templating import templates

    spec = get_spec(Domain.HOUSE)
    form_fields = build_form_fields(session, spec, "house_appliance", {}, {})
    partial = templates.env.get_template("domains/_field_inputs.html")
    first = partial.render(form_fields=form_fields, id_prefix="doc-1-")
    second = partial.render(form_fields=form_fields, id_prefix="doc-2-")
    plain = partial.render(form_fields=form_fields)

    ids_first, ids_second = set(re.findall(r'id="([^"]+)"', first)), set(re.findall(r'id="([^"]+)"', second))
    assert ids_first and ids_first.isdisjoint(ids_second)
    assert 'for="doc-1-f-item_name"' in first and 'list="doc-1-list-item_name"' in first
    assert 'id="f-item_name"' in plain  # default: no prefix


def test_upload_creates_a_house_document_and_redirects(client, session):
    response = _upload(client, {"category": "house_appliance", "item_name": "Boiler", "room": "Kitchen"})

    assert response.status_code == 303
    document_id = int(response.headers["location"].rsplit("/", 1)[-1])
    document = session.get(Document, document_id)
    assert response.headers["location"] == f"/house/documents/{document_id}"
    assert document.domain == Domain.HOUSE and document.category == "house_appliance"
    assert json.loads(document.fields_json) == {"item_name": "Boiler", "room": "Kitchen"}


def test_upload_with_missing_fields_rerenders_with_errors_and_stores_nothing(client, session):
    response = _upload(client, {"category": "maintenance_log", "item_name": "Boiler"})

    assert response.status_code == 400
    assert "Service date is required" in response.text
    assert session.exec(select(Document)).all() == []


def test_upload_rejects_video_for_a_manual(client, session):
    response = _upload(client, {"category": "house_appliance", "item_name": "Boiler"},
                       filename="demo.mp4", content=b"v", mime="video/mp4")
    assert response.status_code == 400
    assert "accepts" in response.text


def test_upload_without_a_file_is_rejected(client):
    response = client.post("/house/upload", data={"category": "ownership_document"})
    assert response.status_code == 400
    assert "Choose a file" in response.text


def test_room_suggestions_offer_existing_values(client, session):
    _upload(client, {"category": "house_appliance", "item_name": "Boiler", "room": "Kitchen"})
    form = client.get("/house/upload/fields?category=house_appliance").text
    assert '<option value="Kitchen">' in form


def test_document_page_shows_fields_note_and_item_page_link(client, session):
    response = _upload(client, {"category": "warranty_invoice", "item_name": "Boiler", "room": "Kitchen"})
    page = client.get(response.headers["location"])

    assert page.status_code == 200
    assert "Warranty / invoice" in page.text and "Boiler" in page.text
    assert "enter it manually" in page.text
    assert 'href="/wiki/' in page.text  # the Boiler item page


def test_editing_fields_sets_the_expiry_and_creates_the_reminder(client, session):
    location = _upload(client, {"category": "warranty_invoice", "item_name": "Boiler"}).headers["location"]

    response = client.post(f"{location}/fields", data={"item_name": "Boiler", "warranty_expiry": "2030-03-01"},
                           follow_redirects=False)

    assert response.status_code == 303
    todo = session.exec(select(Todo)).one()
    assert todo.title == "Renew Boiler warranty" and todo.due_date == date(2030, 2, 8)


def test_editing_with_invalid_fields_shows_errors(client):
    location = _upload(client, {"category": "warranty_invoice", "item_name": "Boiler"}).headers["location"]
    response = client.post(f"{location}/fields", data={"item_name": "", "warranty_expiry": "soon"})
    assert response.status_code == 400
    assert "Item is required" in response.text


def test_non_house_documents_are_404(client, session):
    document = Document(filename="edp.pdf", file_path="/tmp/edp.pdf", content_hash="f1",
                        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED, domain=Domain.FINANCIALS)
    session.add(document)
    session.commit()
    session.refresh(document)
    assert client.get(f"/house/documents/{document.id}").status_code == 404
    assert client.get("/house/documents/99999").status_code == 404
