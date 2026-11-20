"""PLAN step 6c, language (Pedro, 2026-11-19): areas, topics and wiki guidance in English; a paper's own summary stays
in its language. Existing Portuguese names get English ones, the Portuguese kept as an alias."""

from datetime import date

from sqlmodel import select

from app.categories.health import areas as areas_svc
from app.categories.health.extraction import _SYSTEM, ExtractedHealth
from app.categories.health.store import apply_extraction, entry_for
from app.models.category import Category
from app.models.wiki import TOPIC_PAGE_TYPE, WikiPage
from app.services import english_names
from app.services.wiki_engine import _SYSTEM_TEMPLATE
from app.services.wiki_store import ClaimInput, PageRef, apply_claims
from tests.knowledge_factories import make_document


def _portuguese(session):
    doc = make_document(session, category=Category.HEALTH, paper_kind="lab_result")
    apply_extraction(session, doc, ExtractedHealth(person_name="Pedro Almeida", document_date=date(2026, 3, 2),
                                                   provider="Lab", record_kind="test", summary="Análises normais.",
                                                   layout="sheet", areas=["Sangue", "Blood"],
                                                   conditions=["AVC isquémico"]))
    apply_claims(session, [ClaimInput(page=PageRef(page_type=TOPIC_PAGE_TYPE, title="Vicente: olhos"),
                                      key="last_exam", value="2026-06")], category=Category.HEALTH)
    session.commit()
    return entry_for(session, doc.id)


def test_the_readers_are_told_to_name_in_english_and_keep_the_paper_wording():
    assert "write a new name in English" in _SYSTEM and "Write it in the language of the paper" in _SYSTEM
    assert "titles and fact names in English" in _SYSTEM_TEMPLATE and "as the document prints it" in _SYSTEM_TEMPLATE


def test_portuguese_names_become_english_and_still_lead_there(session):
    entry = _portuguese(session)
    todo = english_names.plan(session)
    assert ("area", "Sangue", "Blood") in todo.merges and ("condition", "AVC isquémico", "Ischaemic stroke") in todo.merges
    assert todo.renames == [("Vicente: olhos", "Vicente: eyes")]
    english_names.apply(session)
    assert sorted(a.name for a in areas_svc.tags_of(session, entry.id)) == ["Blood", "Ischaemic stroke"]
    assert areas_svc.resolve_area(session, "Sangue", "area").name == "Blood"            # the alias still leads there
    page = session.exec(select(WikiPage)).one()
    assert page.topic == "Vicente: eyes"
    apply_claims(session, [ClaimInput(page=PageRef(page_type=TOPIC_PAGE_TYPE, title="Vicente: olhos"),
                                      key="glasses", value="yes")], category=Category.HEALTH)
    assert len(session.exec(select(WikiPage)).all()) == 1                                # the old title lands here
    assert english_names.plan(session).merges == [] and english_names.plan(session).renames == []
