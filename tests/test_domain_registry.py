import pytest

from app.categories import registry
from app.categories.base import (
    KindSpec, FieldKind, FieldSpec, MediaKind, UnknownKindError,
)
from app.models.document import Document, DocumentSource
from app.models.category import Category
from tests.domain_fakes import make_fake_spec


def test_category_lookup_and_labels():
    spec = make_fake_spec()
    assert spec.kind("manual").label == "Manual"
    assert spec.kind_label("clip") == "Clip"
    assert spec.kind_label("unknown") == "unknown"
    assert spec.kind_label(None) == ""
    with pytest.raises(UnknownKindError):
        spec.kind("nope")


def test_spec_rejects_duplicate_categories_and_reserved_field_keys():
    import dataclasses

    spec = make_fake_spec()
    with pytest.raises(ValueError):
        dataclasses.replace(spec, kinds=spec.kinds + (KindSpec("manual", "Again", "dup"),))
    with pytest.raises(ValueError):
        dataclasses.replace(spec, fields=spec.fields + (FieldSpec("category", "Category", FieldKind.TEXT),))


def test_fields_for_filters_by_category_and_media():
    spec = make_fake_spec()
    assert [f.key for f in spec.fields_for("manual")] == ["item_name", "seen_on"]
    assert [f.key for f in spec.fields_for("clip", MediaKind.PDF)] == ["side", "seen_on"]
    assert [f.key for f in spec.fields_for("clip", MediaKind.VIDEO)] == ["side", "seen_on", "observations"]
    # No media known yet (e.g. rendering an empty upload form): media-limited fields are included.
    assert [f.key for f in spec.fields_for("clip")] == ["side", "seen_on", "observations"]


def test_get_spec_and_unknown_domain(fake_domain):
    assert registry.get_spec(Category.HOUSE) is fake_domain
    assert registry.is_implemented(Category.HOUSE)
    assert not registry.is_implemented(Category.FINANCIALS)
    assert not registry.is_implemented(None)
    with pytest.raises(registry.UnknownCategoryError):
        registry.get_spec(Category.FINANCIALS)


def test_document_url_is_none_until_finalized(fake_domain):
    unfinalized = Document(id=7, filename="a.pdf", file_path="/tmp/a.pdf", content_hash="h", source=DocumentSource.MANUAL)
    finalized = Document(id=8, filename="b.pdf", file_path="/tmp/b.pdf", content_hash="h2",
                         source=DocumentSource.MANUAL, category=Category.HOUSE)
    assert registry.document_url(unfinalized) is None
    assert registry.document_url(finalized) == "/fake/documents/8"


def test_registry_loads_spec_modules_in_order(monkeypatch):
    monkeypatch.setattr(registry, "_SPEC_MODULES", ("tests.domain_fakes",))
    monkeypatch.setattr(registry, "_specs_cache", None)
    assert [s.label for s in registry.implemented_domains()] == ["Fake"]


def test_document_url_points_pending_review_documents_to_the_inbox(fake_domain):
    from app.models.document import DocumentStatus

    pending = Document(id=9, filename="a.pdf", file_path="/tmp/a.pdf", content_hash="h9",
                       source=DocumentSource.EMAIL, status=DocumentStatus.PENDING_REVIEW)
    discarded = Document(id=10, filename="b.pdf", file_path="/tmp/b.pdf", content_hash="h10",
                         source=DocumentSource.EMAIL, status=DocumentStatus.DISCARDED)
    assert registry.document_url(pending) == "/inbox#inbox-9"
    assert registry.document_url(discarded) is None