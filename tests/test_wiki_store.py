import json

from sqlmodel import select

from app.domains.base import FactPolicy
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.wiki import ClaimStatus, WikiClaim, WikiLogEntry, WikiOperation, WikiPage
from app.services import wiki_store
from app.services.wiki_store import ClaimInput, PageRef

BOILER = PageRef(page_type="fake.item", title="Boiler", entity_key="boiler")


def _document(session, name="a.pdf", content_hash="h-a"):
    document = Document(filename=name, file_path=f"/tmp/{name}", content_hash=content_hash,
                        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED, domain=Domain.HOUSE)
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def _claims(session, page_id, status=None):
    query = select(WikiClaim).where(WikiClaim.page_id == page_id)
    if status is not None:
        query = query.where(WikiClaim.status == status)
    return session.exec(query.order_by(WikiClaim.id)).all()


def test_normalize_entity_key():
    assert wiki_store.normalize_entity_key("  Heat   PUMP ") == "heat pump"


def test_new_claim_creates_entity_page_with_source_and_log(session):
    document = _document(session)

    report = wiki_store.apply_claims(
        session, [ClaimInput(BOILER, "room", "Kitchen", label="Room")], document=document,
    )

    page = session.exec(select(WikiPage).where(WikiPage.entity_key == "boiler")).one()
    assert page.topic == "Boiler" and page.domain == Domain.HOUSE and page.page_type == "fake.item"
    assert json.loads(page.facts_json) == {"room": "Kitchen"}
    assert page.summary == "Room: Kitchen"
    claim = _claims(session, page.id)[0]
    assert claim.status == ClaimStatus.ACTIVE
    assert [d.id for d in wiki_store.sources_for_claims(session, [claim.id])[claim.id]] == [document.id]
    assert report.added == 1 and report.page_ids == [page.id]
    entry = session.get(WikiLogEntry, report.log_entry_id)
    assert entry.operation == WikiOperation.INGEST and entry.document_id == document.id
    assert json.loads(entry.page_ids_json) == [page.id]
    assert "a.pdf" in entry.description and "Boiler" in entry.description


def test_same_value_from_another_document_is_confirmed_not_duplicated(session):
    first, second = _document(session), _document(session, "b.pdf", "h-b")
    wiki_store.apply_claims(session, [ClaimInput(BOILER, "room", "Kitchen")], document=first)

    report = wiki_store.apply_claims(session, [ClaimInput(BOILER, "room", "Kitchen")], document=second)

    page = wiki_store.find_page(session, BOILER)
    claims = _claims(session, page.id)
    assert len(claims) == 1 and report.confirmed == 1 and report.added == 0
    assert {d.id for d in wiki_store.sources_for_claims(session, [claims[0].id])[claims[0].id]} == {first.id, second.id}


def test_changed_value_supersedes_never_deletes(session):
    first, second = _document(session), _document(session, "b.pdf", "h-b")
    wiki_store.apply_claims(session, [ClaimInput(BOILER, "room", "Kitchen")], document=first)

    report = wiki_store.apply_claims(session, [ClaimInput(BOILER, "room", "Utility room")], document=second)

    page = wiki_store.find_page(session, BOILER)
    old, new = _claims(session, page.id)
    assert old.status == ClaimStatus.SUPERSEDED and old.superseded_by_claim_id == new.id and old.superseded_at
    assert new.status == ClaimStatus.ACTIVE and new.value == "Utility room"
    assert json.loads(page.facts_json) == {"room": "Utility room"}
    assert [c.id for c in wiki_store.superseded_claims(session, page.id)] == [old.id]
    assert report.superseded == 1


def test_latest_policy_keeps_the_newer_date(session):
    newer, older = _document(session), _document(session, "b.pdf", "h-b")
    wiki_store.apply_claims(session, [ClaimInput(BOILER, "last_serviced", "2026-06-01", policy=FactPolicy.LATEST)], document=newer)

    wiki_store.apply_claims(session, [ClaimInput(BOILER, "last_serviced", "2025-01-10", policy=FactPolicy.LATEST)], document=older)

    page = wiki_store.find_page(session, BOILER)
    active = wiki_store.active_claims(session, page.id)
    assert [c.value for c in active] == ["2026-06-01"]
    recorded_old = _claims(session, page.id, ClaimStatus.SUPERSEDED)[0]
    assert recorded_old.value == "2025-01-10" and recorded_old.superseded_by_claim_id == active[0].id


def test_latest_policy_accepts_a_correction_from_the_same_document(session):
    document = _document(session)
    wiki_store.apply_claims(session, [ClaimInput(BOILER, "warranty_expires", "2029-01-01", policy=FactPolicy.LATEST)], document=document)

    wiki_store.apply_claims(session, [ClaimInput(BOILER, "warranty_expires", "2028-01-01", policy=FactPolicy.LATEST)], document=document)

    page = wiki_store.find_page(session, BOILER)
    assert [c.value for c in wiki_store.active_claims(session, page.id)] == ["2028-01-01"]


def test_entity_title_collision_with_a_topic_page_gets_a_suffix(session):
    session.add(WikiPage(topic="Boiler"))
    session.commit()
    wiki_store.apply_claims(session, [ClaimInput(BOILER, "room", "Kitchen")], document=_document(session))
    assert wiki_store.find_page(session, BOILER).topic == "Boiler (fake.item)"


def test_topic_pages_keep_the_given_summary(session):
    ref = PageRef(page_type="topic", title="Electricity — provider & contract", summary="EDP, bi-hourly tariff")
    wiki_store.apply_claims(session, [ClaimInput(ref, "provider", "EDP")], document=_document(session))
    assert wiki_store.find_page(session, ref).summary == "EDP, bi-hourly tariff"


def test_claims_without_a_source_document(session):
    ref = PageRef(page_type="answer", title="Which warranties expire this year?")
    report = wiki_store.apply_claims(
        session, [ClaimInput(ref, "answer", "Boiler (March)")], document=None, domain=None,
        operation=WikiOperation.QUERY, description="Saved answer",
    )
    page = wiki_store.find_page(session, ref)
    claim = wiki_store.active_claims(session, page.id)[0]
    assert wiki_store.sources_for_claims(session, [claim.id]) == {}
    entry = session.get(WikiLogEntry, report.log_entry_id)
    assert entry.operation == WikiOperation.QUERY and entry.document_id is None


def test_recent_log_is_newest_first(session):
    wiki_store.append_log(session, WikiOperation.LINT, "first")
    wiki_store.append_log(session, WikiOperation.LINT, "second")
    assert [e.description for e in wiki_store.recent_log(session)] == ["second", "first"]


def test_build_wiki_index_groups_by_domain_and_page_type(session, fake_domain):
    wiki_store.apply_claims(session, [ClaimInput(BOILER, "room", "Kitchen", label="Room")], document=_document(session))
    session.add(WikiPage(topic="House insurance", domain=Domain.HOUSE, summary="Fidelidade policy"))
    session.add(WikiPage(topic="Legacy", facts_json='{"provider": "EDP"}'))
    session.commit()

    sections = wiki_store.build_wiki_index(session)

    assert [(s.domain_label, s.section_label) for s in sections] == [
        ("Fake", "Items"), ("Fake", "Topics"), ("General", "Topics"),
    ]
    assert sections[0].entries[0].title == "Boiler" and sections[0].entries[0].summary == "Room: Kitchen"
    assert sections[2].entries[0].summary == "provider: EDP"


from app.services.wiki_store import LinkInput

KITCHEN = PageRef(page_type="fake.room", title="Kitchen", entity_key="kitchen")


def test_add_link_is_idempotent_and_directional(session):
    a = wiki_store.ensure_page(session, BOILER, Domain.HOUSE)
    b = wiki_store.ensure_page(session, KITCHEN, Domain.HOUSE)
    session.commit()

    assert wiki_store.add_link(session, a.id, b.id) is True
    assert wiki_store.add_link(session, a.id, b.id) is False
    assert wiki_store.add_link(session, a.id, a.id) is False
    assert [p.topic for p in wiki_store.links_from(session, a.id)] == ["Kitchen"]
    assert [p.topic for p in wiki_store.links_to(session, b.id)] == ["Boiler"]
    assert wiki_store.links_from(session, b.id) == []


def test_apply_claims_writes_bidirectional_links_and_logs_linked_pages(session):
    document = _document(session)

    report = wiki_store.apply_claims(
        session, [ClaimInput(BOILER, "room", "Kitchen")], document=document, links=[LinkInput(BOILER, KITCHEN)],
    )

    boiler, kitchen = wiki_store.find_page(session, BOILER), wiki_store.find_page(session, KITCHEN)
    assert kitchen is not None and kitchen.domain == Domain.HOUSE
    assert [p.id for p in wiki_store.links_from(session, boiler.id)] == [kitchen.id]
    assert [p.id for p in wiki_store.links_from(session, kitchen.id)] == [boiler.id]
    assert report.links_added == 2
    assert set(json.loads(session.get(WikiLogEntry, report.log_entry_id).page_ids_json)) == {boiler.id, kitchen.id}

    again = wiki_store.apply_claims(session, [], document=document, links=[LinkInput(BOILER, KITCHEN)])
    assert again.links_added == 0


def test_answer_pages_are_indexed_as_saved_answers(session):
    ref = PageRef(page_type="answer", title="Which warranties expire this year?")
    wiki_store.apply_claims(session, [ClaimInput(ref, "answer", "Boiler (March)")], document=None,
                            operation=WikiOperation.QUERY, description="Saved answer")
    assert [(s.domain_label, s.section_label) for s in wiki_store.build_wiki_index(session)] == [("General", "Saved answers")]


from app.models.record import Record


def _record(session, fields=None):
    record = Record(domain=Domain.HOUSE, category="visit", fields_json=json.dumps(fields or {}))
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def test_a_record_can_be_a_claim_source_and_is_logged(session):
    record = _record(session)
    report = wiki_store.apply_claims(session, [ClaimInput(BOILER, "visited", "2026-02-01")], document=None, record=record)
    page = wiki_store.find_page(session, BOILER)
    claim = wiki_store.active_claims(session, page.id)[0]
    assert wiki_store.claim_sources(session, [claim.id])[claim.id] == [record]
    entry = session.get(WikiLogEntry, report.log_entry_id)
    assert entry.record_id == record.id and entry.document_id is None and page.domain == Domain.HOUSE


def test_a_record_source_default_description_uses_the_category_label_not_the_raw_key(session, fake_domain):
    record = _record(session)
    report = wiki_store.apply_claims(session, [ClaimInput(BOILER, "visited", "2026-02-01")], document=None, record=record)
    entry = session.get(WikiLogEntry, report.log_entry_id)
    assert entry.description.startswith(f"Visit (entered by hand) #{record.id} → ")
    assert "visit record" not in entry.description


def test_note_change_is_a_new_claim(session):
    document = _document(session)
    wiki_store.apply_claims(session, [ClaimInput(BOILER, "warranty_expires", "2029-03-01", note="assumed")], document=document)
    wiki_store.apply_claims(session, [ClaimInput(BOILER, "warranty_expires", "2029-03-01")], document=document)
    page = wiki_store.find_page(session, BOILER)
    (active,) = wiki_store.active_claims(session, page.id)
    assert active.note is None
    assert [c.note for c in wiki_store.superseded_claims(session, page.id)] == ["assumed"]


def test_retract_missing_supersedes_sole_source_claims_and_withdraws_shared_links(session):
    first, second = _document(session), _document(session, "b.pdf", "h-b")
    wiki_store.apply_claims(session, [ClaimInput(BOILER, "room", "Kitchen"), ClaimInput(BOILER, "type", "Oven")], document=first)
    wiki_store.apply_claims(session, [ClaimInput(BOILER, "room", "Kitchen")], document=second)

    report = wiki_store.apply_claims(session, [], document=first, retract_missing=True)

    page = wiki_store.find_page(session, BOILER)
    active = {c.key: c for c in wiki_store.active_claims(session, page.id)}
    assert set(active) == {"room"}  # still supported by `second`
    assert [d.id for d in wiki_store.claim_sources(session, [active["room"].id])[active["room"].id]] == [second.id]
    (gone,) = wiki_store.superseded_claims(session, page.id)
    assert gone.key == "type" and gone.superseded_by_claim_id is None
    assert report.retracted == 2