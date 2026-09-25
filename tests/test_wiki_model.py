from app.models.wiki import WikiChange, WikiPage


def test_create_wiki_page_and_change(session):
    page = WikiPage(topic="Electricity — provider & contract", facts_json='{"provider": "EDP"}')
    session.add(page)
    session.commit()
    session.refresh(page)

    change = WikiChange(
        wiki_page_id=page.id, fact_key="provider", old_value=None, new_value="EDP",
    )
    session.add(change)
    session.commit()
    session.refresh(change)

    assert page.id is not None
    assert change.wiki_page_id == page.id


def test_wiki_page_domain_defaults_to_none(session):
    page = WikiPage(topic="Water — provider & contract", facts_json="{}")
    session.add(page)
    session.commit()
    session.refresh(page)

    assert page.domain is None


def test_wiki_page_domain_can_be_set(session):
    from app.models.domain import Domain

    page = WikiPage(
        topic="Electricity — provider & contract", facts_json='{"provider": "EDP"}',
        domain=Domain.FINANCIALS,
    )
    session.add(page)
    session.commit()
    session.refresh(page)

    assert page.domain == Domain.FINANCIALS


import pytest
from sqlalchemy.exc import IntegrityError

from app.models.document import Document, DocumentSource
from app.models.wiki import ClaimStatus, WikiClaim, WikiClaimSource, WikiLogEntry, WikiOperation


def test_wiki_page_defaults_to_topic_type(session):
    page = WikiPage(topic="Water")
    session.add(page)
    session.commit()
    session.refresh(page)
    assert page.page_type == "topic"
    assert page.entity_key is None and page.summary is None


def test_entity_pages_are_unique_per_type_and_key(session):
    session.add(WikiPage(topic="Boiler", page_type="house.item", entity_key="boiler"))
    session.commit()
    session.add(WikiPage(topic="Boiler (again)", page_type="house.item", entity_key="boiler"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_claim_with_source_and_supersession(session):
    document = Document(filename="w.pdf", file_path="/tmp/w.pdf", content_hash="hw", source=DocumentSource.MANUAL)
    page = WikiPage(topic="Boiler", page_type="house.item", entity_key="boiler")
    session.add(document)
    session.add(page)
    session.commit()
    session.refresh(page)
    session.refresh(document)

    old = WikiClaim(page_id=page.id, key="warranty_expires", label="Warranty expires", value="2026-01-01")
    session.add(old)
    session.commit()
    session.refresh(old)
    assert old.status == ClaimStatus.ACTIVE and old.superseded_at is None

    new = WikiClaim(page_id=page.id, key="warranty_expires", value="2028-01-01")
    session.add(new)
    session.commit()
    session.refresh(new)
    old.status = ClaimStatus.SUPERSEDED
    old.superseded_by_claim_id = new.id
    session.add(old)
    session.add(WikiClaimSource(claim_id=new.id, document_id=document.id))
    session.commit()

    session.refresh(old)
    assert old.superseded_by_claim_id == new.id


def test_log_entry_defaults(session):
    entry = WikiLogEntry(operation=WikiOperation.INGEST, description="w.pdf → Boiler")
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.page_ids_json == "[]" and entry.document_id is None and entry.occurred_at is not None


def test_wiki_links_are_unique_per_direction(session):
    from app.models.wiki import WikiLink

    a, b = WikiPage(topic="Boiler"), WikiPage(topic="Kitchen")
    session.add(a)
    session.add(b)
    session.commit()
    session.add(WikiLink(from_page_id=a.id, to_page_id=b.id))
    session.add(WikiLink(from_page_id=b.id, to_page_id=a.id))
    session.commit()
    session.add(WikiLink(from_page_id=a.id, to_page_id=b.id))
    with pytest.raises(IntegrityError):
        session.commit()


def test_review_operation_exists():
    assert WikiOperation.REVIEW.value == "review"
