import json
from datetime import date

import pytest

from app.domains.health import areas
from app.domains.health.ask import HEALTH_TOOLS
from app.domains.health.extraction import ExtractedHealth, ExtractedMeasurement
from app.domains.health.store import apply_extraction
from app.models.domain import Domain
from app.privacy.scope import reachable_scope
from app.privacy.tiers import Tier
from tests import privacy_world as pw
from tests.knowledge_factories import make_document

TOOLS = {t.name: t for t in HEALTH_TOOLS}


def _paper(session, person, when, test_value, billed=None, reimbursed=None, areas_=("Heart and circulation",), name="a.pdf"):
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result", filename=name)
    ex = ExtractedHealth(person_name=person, document_date=when, provider="Lab Norte", record_kind="test",
                         areas=list(areas_), conditions=[], summary=f"{person} panel",
                         amount_billed=billed, amount_reimbursed=reimbursed,
                         measurements=[ExtractedMeasurement("LDL cholesterol", test_value, None, "mg/dL", None, 100.0,
                                                            when, False, "LDL")])
    apply_extraction(session, doc, ex)
    return doc


@pytest.fixture()
def world(session):
    pw.set_levels(session, on=False)
    a = _paper(session, "Maria Santos Almeida", date(2024, 3, 1), 150.0, 40.0, 30.0, name="a.pdf")
    b = _paper(session, "Maria Santos Almeida", date(2026, 3, 12), 162.0, 45.5, 0.0, name="b.pdf")
    c = _paper(session, "Pedro Santos Almeida", date(2026, 5, 2), 110.0, 20.0, 20.0, ("Eyes",), name="c.pdf")
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


def test_spending_sums_per_person_and_year(session, world):
    out = run(session, "health_spending", person="Maria", year=2026)
    assert "45.50" in out.text and "reimbursed 0.00" in out.text.replace("  ", " ")
    both = run(session, "health_spending", year=2026)
    assert "Maria Santos Almeida" in both.text and "Pedro Santos Almeida" in both.text


def test_the_tools_see_nothing_outside_health(session, world):
    other = make_document(session, domain=Domain.HOUSE, category="house_appliance", fields={"item_name": "Boiler"})
    assert "Boiler" not in run(session, "health_find_records", query="boiler").text
