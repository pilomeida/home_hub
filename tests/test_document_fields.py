import json

import pytest

from app.domains.base import MediaKind
from app.domains.fields import (
    InvalidClassification, build_form_fields, describe_document, distinct_field_values,
    dump_fields, field_date, load_fields, media_kind_for, validate_fields,
)
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain


def _doc(session, fields, domain=Domain.HOUSE, content_hash="h", category="manual"):
    document = Document(
        filename="x.pdf", file_path="/srv/app/static/documents/abc.pdf", content_hash=content_hash,
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
        domain=domain, category=category, fields_json=json.dumps(fields),
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


@pytest.mark.parametrize("name,kind", [
    ("a.pdf", MediaKind.PDF), ("A.JPG", MediaKind.IMAGE), ("p.jpeg", MediaKind.IMAGE),
    ("p.png", MediaKind.IMAGE), ("p.heic", MediaKind.IMAGE), ("v.mp4", MediaKind.VIDEO),
    ("v.MOV", MediaKind.VIDEO), ("notes.txt", MediaKind.OTHER), ("noext", MediaKind.OTHER),
])
def test_media_kind_for(name, kind):
    assert media_kind_for(name) == kind


def test_load_dump_and_field_date():
    assert load_fields(Document(filename="a", file_path="a", content_hash="h", source=DocumentSource.MANUAL)) == {}
    assert json.loads(dump_fields({"b": "2", "a": "1"})) == {"a": "1", "b": "2"}
    assert field_date({"d": "2027-03-01"}, "d").isoformat() == "2027-03-01"
    assert field_date({"d": "garbage"}, "d") is None
    assert field_date({}, "d") is None


def test_validate_requires_required_fields(session, fake_domain):
    with pytest.raises(InvalidClassification) as exc:
        validate_fields(session, fake_domain, "manual", MediaKind.PDF, {"item_name": "   "})
    assert "item_name" in exc.value.errors


def test_validate_drops_fields_that_do_not_apply(session, fake_domain):
    clean = validate_fields(
        session, fake_domain, "clip", MediaKind.PDF,
        {"side": "in", "observations": "only for photos", "item_name": "not for clips", "unknown": "x"},
    )
    assert clean == {"side": "in"}

    clean_video = validate_fields(session, fake_domain, "clip", MediaKind.VIDEO, {"side": "out", "observations": " leak "})
    assert clean_video == {"side": "out", "observations": "leak"}


def test_validate_dates_and_choices(session, fake_domain):
    assert validate_fields(session, fake_domain, "manual", MediaKind.PDF,
                           {"item_name": "Boiler", "seen_on": "2026-02-03"})["seen_on"] == "2026-02-03"
    with pytest.raises(InvalidClassification) as bad_date:
        validate_fields(session, fake_domain, "manual", MediaKind.PDF, {"item_name": "Boiler", "seen_on": "03/02/2026"})
    assert "seen_on" in bad_date.value.errors
    with pytest.raises(InvalidClassification) as bad_choice:
        validate_fields(session, fake_domain, "clip", MediaKind.PDF, {"side": "sideways"})
    assert "side" in bad_choice.value.errors


def test_suggest_fields_reuse_existing_spelling(session, fake_domain):
    _doc(session, {"item_name": "Boiler"})
    clean = validate_fields(session, fake_domain, "manual", MediaKind.PDF, {"item_name": "  boiler  "})
    assert clean["item_name"] == "Boiler"
    new = validate_fields(session, fake_domain, "manual", MediaKind.PDF, {"item_name": "Heat   pump"})
    assert new["item_name"] == "Heat pump"


def test_distinct_field_values_dedupes_and_is_domain_scoped(session, fake_domain):
    _doc(session, {"item_name": "Kitchen"}, content_hash="1")
    _doc(session, {"item_name": "kitchen"}, content_hash="2")
    _doc(session, {"item_name": "Attic"}, content_hash="3")
    _doc(session, {"item_name": "Garage"}, domain=Domain.FINANCIALS, content_hash="4")
    _doc(session, {}, content_hash="5")
    assert distinct_field_values(session, Domain.HOUSE, "item_name") == ["Attic", "Kitchen"]


def test_describe_document_uses_registry_labels(session, fake_domain):
    document = _doc(session, {"item_name": "Boiler", "seen_on": "2026-01-02"})
    description = describe_document(document)
    assert description.domain_label == "Fake"
    assert description.category_label == "Manual"
    assert description.fields == [("Item", "Boiler"), ("Seen on", "2026-01-02")]
    assert description.url == f"/fake/documents/{document.id}"
    assert description.file_url == "/static/documents/abc.pdf"


def test_build_form_fields_carries_options_values_and_errors(session, fake_domain):
    _doc(session, {"item_name": "Boiler"})
    manual_fields = build_form_fields(session, fake_domain, "manual", {"item_name": "Boi"}, {"item_name": "bad"})
    item = manual_fields[0]
    assert item.spec.key == "item_name" and item.value == "Boi" and item.error == "bad"
    assert item.options == [("Boiler", "Boiler")]
    clip_fields = build_form_fields(session, fake_domain, "clip", {}, {})
    assert clip_fields[0].options == [("in", "Indoor"), ("out", "Outdoor")]


def test_suggestions_include_record_values(session, fake_domain):
    from app.models.record import Record

    session.add(Record(domain=Domain.HOUSE, category="visit", fields_json='{"item_name": "Heat pump"}'))
    session.commit()
    assert distinct_field_values(session, Domain.HOUSE, "item_name") == ["Heat pump"]