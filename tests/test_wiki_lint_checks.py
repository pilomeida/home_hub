import dataclasses
from datetime import datetime

from app.domains import registry
from app.domains.base import LinkSpec
from app.models.document import DocumentStatus
from app.models.wiki import ANSWER_PAGE_TYPE
from app.models.wiki_lint import LintFindingKind
from app.services.wiki_lint import checks
from app.services.wiki_lint.findings import FindingDraft
from tests.domain_fakes import make_fake_spec
from tests.knowledge_factories import (
    make_claim, make_document, make_ingest_log, make_link, make_page, make_record,
)


def test_fingerprint_ignores_order_and_wording():
    a = FindingDraft(LintFindingKind.MISSING_LINK, "x", wiki_page_ids=(2, 1))
    b = FindingDraft(LintFindingKind.MISSING_LINK, "different words", wiki_page_ids=(1, 2))
    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint() != FindingDraft(LintFindingKind.MISSING_LINK, "x", wiki_page_ids=(1, 2), record_ids=(5,)).fingerprint()


def test_orphan_pages(session):
    lonely = make_page(session, "Lawnmower")
    a, b = make_page(session, "Boiler"), make_page(session, "Utility room")
    make_link(session, a, b)
    assert [f.wiki_page_ids for f in checks.find_orphan_pages(session)] == [(lonely.id,)]


def test_unsourced_claims_include_withdrawn_only_and_skip_records_answers_superseded(session, fake_domain):
    doc, old = make_document(session, filename="a.pdf"), make_document(session, filename="old.pdf")
    visit = make_record(session, fields={"item_name": "Boiler", "visit_date": "2026-03-02"})
    page = make_page(session, "Boiler")
    bare = make_claim(session, page, "model", "ecoTEC")
    withdrawn = make_claim(session, page, "colour", "white", withdrawn_doc_ids=[old.id])
    make_claim(session, page, "room", "utility", doc_ids=[doc.id], withdrawn_doc_ids=[old.id])  # still supported
    make_claim(session, page, "visited", "2026-03-02", record_ids=[visit.id])                  # record source counts
    make_claim(session, page, "old", "x", superseded_at=datetime(2026, 1, 1))
    ans = make_page(session, "Saved Q", page_type=ANSWER_PAGE_TYPE)
    make_claim(session, ans, "question", "q")

    found = checks.find_unsourced_claims(session)

    assert len(found) == 1 and set(found[0].claim_ids) == {bare.id, withdrawn.id}


def test_missing_links_by_topic_mention(session):
    boiler, room = make_page(session, "Boiler"), make_page(session, "Utility room")
    make_claim(session, boiler, "location", "Installed in the utility room")
    assert [f.wiki_page_ids for f in checks.find_missing_links(session)] == [(boiler.id, room.id)]
    make_link(session, boiler, room)
    assert checks.find_missing_links(session) == []


def _linked_spec(monkeypatch):
    base = make_fake_spec()
    item_type = dataclasses.replace(base.wiki.entity_types[0], links=(LinkSpec("seen_on", "fake.day"),))
    spec = dataclasses.replace(base, wiki=dataclasses.replace(base.wiki, entity_types=(item_type,)))
    monkeypatch.setattr(registry, "_specs_cache", {spec.domain: spec})


def test_stale_link_when_the_newest_source_names_another_target(session, monkeypatch):
    _linked_spec(monkeypatch)
    old = make_document(session, filename="a.pdf", fields={"item_name": "Boiler", "seen_on": "2026-01-01"},
                        created_at=datetime(2026, 1, 1))
    new = make_record(session, fields={"item_name": "Boiler", "seen_on": "2026-03-01", "visit_date": "2026-03-01"},
                      created_at=datetime(2026, 3, 1))
    item = make_page(session, "Boiler", page_type="fake.item", entity_key="boiler")
    day1 = make_page(session, "2026-01-01", page_type="fake.day", entity_key="2026-01-01")
    day2 = make_page(session, "2026-03-01", page_type="fake.day", entity_key="2026-03-01")
    make_claim(session, item, "type", "Manual", doc_ids=[old.id], record_ids=[new.id])
    make_link(session, item, day1)
    make_link(session, item, day2)

    found = checks.find_stale_links(session)

    assert [f.wiki_page_ids for f in found] == [(item.id, day1.id)]
    assert found[0].kind == LintFindingKind.STALE_LINK and found[0].record_ids == (new.id,)
    assert "2026-03-01" in found[0].summary


def test_withdrawn_source_does_not_decide_the_current_link_target(session, monkeypatch):
    _linked_spec(monkeypatch)
    kept = make_document(session, filename="a.pdf", fields={"item_name": "Boiler", "seen_on": "2026-01-01"},
                         created_at=datetime(2026, 1, 1))
    refiled = make_document(session, filename="b.pdf", fields={"item_name": "Boiler", "seen_on": "2026-03-01"},
                            created_at=datetime(2026, 3, 1))
    item = make_page(session, "Boiler", page_type="fake.item", entity_key="boiler")
    day1 = make_page(session, "2026-01-01", page_type="fake.day", entity_key="2026-01-01")
    make_claim(session, item, "type", "Manual", doc_ids=[kept.id], withdrawn_doc_ids=[refiled.id])
    make_link(session, item, day1)
    assert checks.find_stale_links(session) == []


def test_uningested_sources_cover_documents_and_records(session, fake_domain):
    done_doc = make_document(session, filename="a.pdf")
    make_ingest_log(session, done_doc)
    missing_doc = make_document(session, filename="b.pdf")
    done_rec = make_record(session, fields={"item_name": "Boiler", "visit_date": "2026-03-02"})
    make_ingest_log(session, done_rec)
    attachment = make_document(session, filename="inv.pdf", category="visit")
    attached_rec = make_record(session, fields={"item_name": "Boiler", "visit_date": "2026-04-02"},
                               document_id=attachment.id)
    make_ingest_log(session, attachment)                      # A logs attachment-backed records by document_id
    missing_rec = make_record(session, fields={"item_name": "Dishwasher", "visit_date": "2026-05-02"})
    make_record(session, fields={"item_name": "Old"}, retired=True)
    make_document(session, filename="c.pdf", domain=None, status=DocumentStatus.PENDING_REVIEW)
    make_document(session, filename="d.pdf", status=DocumentStatus.NEEDS_ATTENTION)

    found = checks.find_uningested_sources(session)

    assert len(found) == 1
    assert found[0].document_ids == (missing_doc.id,) and found[0].record_ids == (missing_rec.id,)
    assert attached_rec.id not in found[0].record_ids


def test_stale_saved_answer_when_linked_page_changes_after_save(session):
    boiler = make_page(session, "Boiler")
    ans = make_page(session, "Saved", page_type=ANSWER_PAGE_TYPE)
    make_claim(session, ans, "question", "q", created_at=datetime(2026, 5, 1))
    make_link(session, ans, boiler)
    make_claim(session, boiler, "last_service", "2026-06-01", created_at=datetime(2026, 6, 1))
    assert checks.find_stale_saved_answers(session)[0].wiki_page_ids == (ans.id, boiler.id)
