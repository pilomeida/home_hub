"""PLAN step 6a, 6b: wiki pages point at their subject; a provider written two ways is one subject with aliases, so it
keeps one page and one provider value; fact keys have one spelling. Done when: Rute's facts are found from her
subject, whichever page holds them; Amigo's provider fact keeps one value across bills."""

import json
from datetime import datetime

import pytest
from sqlmodel import select

from app.models.category import Category
from app.models.subject import Subject, SubjectKind
from app.models.wiki import TOPIC_PAGE_TYPE, ClaimStatus, WikiClaim, WikiLink, WikiPage
from app.services import subjects, wiki_subjects
from app.services.ask.tools import _read_wiki_pages
from app.services.wiki_engine import ingest_into_wiki
from app.services.wiki_store import ClaimInput, PageRef, apply_claims, build_wiki_index, normalize_entity_key
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.knowledge_factories import make_document

pytestmark = pytest.mark.usefixtures("levels_not_binding")


def _bill(session):
    return make_document(session, category=Category.FINANCIALS, paper_kind="bill", fields={"provider": "Vodafone"})


async def _ingest(session, document, pages):
    gw = FakeGateway([gateway_text_result(json.dumps({"pages": pages}))])
    return await ingest_into_wiki(session, document, context="a telecom bill", gateway=gw)


def _active(session, page_id, key):
    return [c.value for c in session.exec(select(WikiClaim).where(WikiClaim.page_id == page_id, WikiClaim.key == key,
                                                                 WikiClaim.status == ClaimStatus.ACTIVE)).all()]


def test_keys_have_one_spelling():
    assert wiki_subjects.fold_key("Provider") == "provider"
    assert wiki_subjects.fold_key("Statement period") == "statement_period"
    assert wiki_subjects.fold_key("network_operator") == "network_operator"


@pytest.mark.asyncio
async def test_amigos_provider_keeps_one_value_across_bills_on_one_page(session):
    wiki_subjects.settle(session)                                    # the aliases of step 6b
    await _ingest(session, _bill(session), [{"title": "Telecom — Vodafone Portugal", "summary": "Mobile",
                                             "facts": {"provider": "Vodafone Portugal", "category": "telecom"}}])
    await _ingest(session, _bill(session), [{"title": "Telecom — Amigo", "summary": "Amigo plan",
                                             "facts": {"Provider": "Amigo (Vodafone)", "Category": "telecom"}}])
    await _ingest(session, _bill(session), [{"title": "Telecom — Amigo (Vodafone Portugal)", "summary": "x",
                                             "facts": {"provider": "Amigo"}}])
    [page] = session.exec(select(WikiPage).where(WikiPage.page_type == TOPIC_PAGE_TYPE)).all()
    assert page.topic == "Telecom — Vodafone Portugal" and page.summary == "Mobile"
    claims = session.exec(select(WikiClaim).where(WikiClaim.page_id == page.id)).all()
    assert {c.value for c in claims if c.key == "provider"} == {"Vodafone Portugal"}      # never flipped
    assert not [c for c in claims if c.key != c.key.lower()]
    assert _active(session, page.id, "category") == ["telecom"]


@pytest.mark.asyncio
async def test_a_different_provider_is_a_real_change(session):
    wiki_subjects.settle(session)
    await _ingest(session, _bill(session), [{"title": "Telecom — Vodafone Portugal", "summary": "s",
                                             "facts": {"provider": "Vodafone Portugal"}}])
    await _ingest(session, _bill(session), [{"title": "Telecom — Vodafone Portugal", "summary": "s",
                                             "facts": {"provider": "NOS"}}])
    [page] = session.exec(select(WikiPage)).all()
    assert _active(session, page.id, "provider") == ["NOS"]


def _rute(session):
    full = "RUTE ALEXANDRA DA SILVA CORREIA"
    person = PageRef(page_type="health.person", title=full, entity_key=normalize_entity_key(full))
    topic = PageRef(page_type=TOPIC_PAGE_TYPE, title="Rute: análises")
    doc = make_document(session, category=Category.HEALTH, paper_kind="lab_result")
    apply_claims(session, [ClaimInput(page=person, key="last_record", value="2026-03-02"),
                           ClaimInput(page=topic, key="Ferritin", value="low in 2026-03")], document=doc)
    session.commit()
    return session.exec(select(WikiPage).where(WikiPage.page_type == "health.person")).one(), \
        session.exec(select(WikiPage).where(WikiPage.page_type == TOPIC_PAGE_TYPE)).one()


def test_ruts_facts_are_found_from_her_subject_whichever_page_holds_them(session):
    person_page, topic_page = _rute(session)
    assert person_page.subject_id is not None and topic_page.subject_id == person_page.subject_id
    assert {p.id for p in wiki_subjects.pages_about(session, person_page.subject_id)} == {person_page.id, topic_page.id}
    out = _read_wiki_pages(session, {"page_ids": [person_page.id]})
    assert f"Other pages about the same subject: wiki:{topic_page.id} Rute: análises" in out.text
    entries = [e for s in build_wiki_index(session) for e in s.entries]
    assert any(e.page_id == topic_page.id and e.about == "RUTE ALEXANDRA DA SILVA CORREIA" for e in entries)


def test_a_joined_spelling_of_a_person_leads_to_her_pages(session):
    person_page, _ = _rute(session)
    short = subjects.register_person(session, "Rute")          # a bank connection owner, say
    [proposal] = subjects.pending(session)
    subjects.decide(session, proposal.match.id, True, "pedro")
    assert len(wiki_subjects.pages_about(session, short.id)) == 2


def test_a_topic_title_never_creates_a_person(session):
    before = len(session.exec(select(Subject)).all())
    apply_claims(session, [ClaimInput(page=PageRef(page_type=TOPIC_PAGE_TYPE, title="Matias: escola"),
                                      key="school", value="EB1")])
    page = session.exec(select(WikiPage)).one()
    assert page.subject_id is None and len(session.exec(select(Subject)).all()) == before


def test_merging_two_subjects_carries_their_wiki_pages(session):
    one = subjects.register(session, SubjectKind.COUNTERPARTY, "Galp")
    apply_claims(session, [ClaimInput(page=PageRef(page_type=TOPIC_PAGE_TYPE, title="Gas — Galp Energia"),
                                      key="provider", value="Galp Energia")])
    page = session.exec(select(WikiPage)).one()
    subjects.merge(session, page.subject_id, one.id)
    session.commit()
    session.refresh(page)
    assert page.subject_id == one.id


def _legacy_telecom(session):
    """The live state before step 6 (2026-11-20): two Telecom pages, keys in two spellings, no subjects."""
    vod = WikiPage(topic="Telecom — Vodafone Portugal", page_type=TOPIC_PAGE_TYPE, category=Category.FINANCIALS)
    ami = WikiPage(topic="Telecom — Amigo", page_type=TOPIC_PAGE_TYPE, category=Category.FINANCIALS)
    other = WikiPage(topic="Water — SMAS Mafra", page_type=TOPIC_PAGE_TYPE, category=Category.FINANCIALS)
    session.add_all([vod, ami, other])
    session.flush()
    t = datetime(2026, 10, 1)
    rows = [(vod, "provider", "Vodafone Portugal", "active", 1), (vod, "category", "telecom", "active", 1),
            (ami, "Provider", "Amigo (Vodafone Portugal)", "active", 2), (ami, "provider", "Amigo", "active", 3),
            (ami, "Category", "Telecom", "active", 2), (ami, "provider", "Amigo (Vodafone)", "superseded", 1),
            (ami, "service_start", "2026-07", "active", 2)]
    for page, key, value, status, day in rows:
        session.add(WikiClaim(page_id=page.id, key=key, label=key, value=value, status=ClaimStatus(status),
                              created_at=t.replace(day=day)))
    session.add(WikiLink(from_page_id=ami.id, to_page_id=other.id))
    session.commit()
    return vod, ami


def test_settle_joins_the_two_telecom_pages_and_folds_their_keys(session):
    vod, ami = _legacy_telecom(session)
    ami_id = ami.id
    out = wiki_subjects.settle(session)
    session.commit()
    assert out["pages_joined"] == ["Telecom — Amigo → Telecom — Vodafone Portugal"]
    assert session.get(WikiPage, ami_id) is None
    assert _active(session, vod.id, "provider") == ["Vodafone Portugal"]
    assert _active(session, vod.id, "service_start") == ["2026-07"]            # nothing the Amigo page knew is lost
    assert len(_active(session, vod.id, "category")) == 1
    assert not session.exec(select(WikiClaim).where(WikiClaim.key == "Provider")).all()
    assert [(l.from_page_id) for l in session.exec(select(WikiLink)).all()] == [vod.id]
    assert all(p.subject_id is not None for p in session.exec(select(WikiPage)).all())
    assert wiki_subjects.settle(session)["pages_joined"] == []                # safe to run again
