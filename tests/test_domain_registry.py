import pytest

from app.domains import registry
from app.domains.base import (
    CategorySpec, FieldKind, FieldSpec, MediaKind, UnknownCategoryError,
)
from app.models.document import Document, DocumentSource
from app.models.domain import Domain
from tests.domain_fakes import make_fake_spec


def test_category_lookup_and_labels():
    spec = make_fake_spec()
    assert spec.category("manual").label == "Manual"
    assert spec.category_label("clip") == "Clip"
    assert spec.category_label("unknown") == "unknown"
    assert spec.category_label(None) == ""
    with pytest.raises(UnknownCategoryError):
        spec.category("nope")


def test_spec_rejects_duplicate_categories_and_reserved_field_keys():
    import dataclasses

    spec = make_fake_spec()
    with pytest.raises(ValueError):
        dataclasses.replace(spec, categories=spec.categories + (CategorySpec("manual", "Again", "dup"),))
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
    assert registry.get_spec(Domain.HOUSE) is fake_domain
    assert registry.is_implemented(Domain.HOUSE)
    assert not registry.is_implemented(Domain.FINANCIALS)
    assert not registry.is_implemented(None)
    with pytest.raises(registry.UnknownDomainError):
        registry.get_spec(Domain.FINANCIALS)


def test_document_url_is_none_until_finalized(fake_domain):
    unfinalized = Document(id=7, filename="a.pdf", file_path="/tmp/a.pdf", content_hash="h", source=DocumentSource.MANUAL)
    finalized = Document(id=8, filename="b.pdf", file_path="/tmp/b.pdf", content_hash="h2",
                         source=DocumentSource.MANUAL, domain=Domain.HOUSE)
    assert registry.document_url(unfinalized) is None
    assert registry.document_url(finalized) == "/fake/documents/8"


def test_registry_loads_spec_modules_in_order(monkeypatch):
    monkeypatch.setattr(registry, "_SPEC_MODULES", ("tests.domain_fakes",))
    monkeypatch.setattr(registry, "_specs_cache", None)
    assert [s.label for s in registry.implemented_domains()] == ["Fake"]