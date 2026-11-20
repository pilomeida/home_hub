import json
from datetime import date

import pytest

from app.categories.health import areas
from app.categories.health.ask import HEALTH_TOOLS
from app.categories.health.extraction import ExtractedHealth, ExtractedMeasurement
from app.categories.health.store import apply_extraction
from app.models.category import Category
from app.privacy.scope import reachable_scope
from app.privacy.tiers import Tier
from tests import privacy_world as pw
from tests.knowledge_factories import make_document

TOOLS = {t.name: t for t in HEALTH_TOOLS}


def _paper(session, person, when, test_value, areas_=("Heart and circulation",), name="a.pdf"):
    doc = make_document(session, category=Category.HEALTH, paper_kind="lab_result", filename=name)
    ex = ExtractedHealth(person_name=person, document_date=when, provider="Lab Norte", record_kind="test",
                         areas=list(areas_), conditions=[], summary=f"{person} panel",
                         measurements=[ExtractedMeasurement("LDL cholesterol", test_value, None, "mg/dL", None, 100.0,
                                                            when, False, "LDL")])
    apply_extraction(session, doc, ex)
    return doc


@pytest.fixture()
def world(session):
    pw.set_levels(session, on=False)
    a = _paper(session, "Maria Santos Almeida", date(2024, 3, 1), 150.0, name="a.pdf")
    b = _paper(session, "Maria Santos Almeida", date(2026, 3, 12), 162.0, name="b.pdf")
    c = _paper(session, "Pedro Santos Almeida", date(2026, 5, 2), 110.0, ("Eyes",), name="c.pdf")
    return a, b, c


def run(session, name, **args):
    with reachable_scope(session):
        return TOOLS[name].run(session, args)


def test_every_health_tool_is_prefixed_and_reports_high_even_with_no_match(session):
    for tool in HEALTH_TOOLS:
        assert tool.name.startswith("health_")
    out = run(session, "health_find_records", query="nothing here")
    assert out.tier == Tier.HIGH and "0" in out.text


def test_find_records_filters_by_person_year_and_area_and_cites_the_papers(session, world):
    a, b, c = world
    out = run(session, "health_find_records", person="Maria", year=2026)
    assert out.tier == Tier.HIGH and f"doc:{b.id}" in out.text and f"doc:{a.id}" not in out.text
    assert [c_.ref for c_ in out.citables] == [f"doc:{b.id}"]
    out = run(session, "health_find_records", area="Eyes")
    assert f"doc:{c.id}" in out.text and f"doc:{b.id}" not in out.text


def test_measurements_come_back_oldest_first_with_range_flags(session, world):
    out = run(session, "health_measurements", person="Maria", test="ldl")
    lines = [l for l in out.text.splitlines() if "LDL" in l]
    assert len(lines) == 2 and lines[0].startswith("2024-03-01") and lines[1].startswith("2026-03-12")
    assert "above range" in lines[1] and out.tier == Tier.HIGH


def test_the_tools_see_nothing_outside_health(session, world):
    other = make_document(session, category=Category.HOUSE, paper_kind="house_appliance", fields={"item_name": "Boiler"})
    assert "Boiler" not in run(session, "health_find_records", query="boiler").text


def test_conditions_tool_reports_high_and_shows_only_readable_papers(session, world):
    """Step 7d: a condition with its papers, follow-up and medication; a paper the AI may not read is not listed."""
    from datetime import timedelta
    from app.categories.health import care, conditions as cs, follow_ups as fu
    from app.categories.health.store import entry_for
    a, b, c = world
    e = entry_for(session, b.id)
    cond = cs.create(session, e.person_id, "Hypercholesterolaemia", "pedro@x", confirm_entry_id=e.id)
    fu.add(session, 0, "Repeat the lipid panel", date.today() + timedelta(days=90), "pedro@x", condition_id=cond.id)
    care.add_medication(session, cond.id, name="Atorvastatin", dose="20 mg")
    out = run(session, "health_conditions", person="Maria")
    assert out.tier == Tier.HIGH and "Hypercholesterolaemia" in out.text and f"doc:{b.id}" in out.text
    assert "Repeat the lipid panel" in out.text and "Atorvastatin 20 mg" in out.text
    assert "Pedro" not in out.text
    from app.models.document import DocumentStatus
    b.status = DocumentStatus.NEEDS_ATTENTION             # a paper not settled is never read by the AI
    session.add(b)
    session.commit()
    out = run(session, "health_conditions", person="Maria")
    assert "Hypercholesterolaemia" in out.text and f"doc:{b.id}" not in out.text and "none readable" in out.text
    assert " |  | " not in out.text
