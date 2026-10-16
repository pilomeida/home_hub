from datetime import date

import pytest

from app.domains.health import areas as areas_svc
from app.domains.health.browse import Filters, facets, parse_filters, rows
from app.domains.health.extraction import ExtractedHealth, ExtractedMeasurement
from app.domains.health.store import apply_extraction
from app.models.domain import Domain
from tests.knowledge_factories import make_document


def _paper(session, person, when, kind, area_names, conditions=(), doubtful=False, name="x.pdf"):
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result", filename=name)
    ex = ExtractedHealth(person_name=person, document_date=when, provider="Lab", record_kind=kind,
                         areas=list(area_names), conditions=list(conditions), summary="s",
                         measurements=[ExtractedMeasurement("LDL", 1.0, None, None if doubtful else "mg/dL", None, None,
                                                            when, False, "")])
    apply_extraction(session, doc, ex)
    return doc


@pytest.fixture()
def world(session):
    a = _paper(session, "Maria Santos Almeida", date(2024, 3, 1), "check_up", ["Heart"], ["Stroke"], name="a.pdf")
    b = _paper(session, "Maria Santos Almeida", date(2026, 3, 12), "test", ["Eyes"], name="b.pdf")
    c = _paper(session, "Pedro Santos Almeida", date(2026, 5, 2), "check_up", [], doubtful=True, name="c.pdf")
    for area in session.exec(__import__("sqlmodel").select(areas_svc.HealthArea)).all():
        if area.name in ("Heart", "Eyes"):
            areas_svc.approve_area(session, area.id)
    return a, b, c


def test_rows_are_newest_first_and_filter_by_every_facet(session, world):
    a, b, c = world
    assert [r.document.id for r in rows(session, Filters())] == [c.id, b.id, a.id]
    maria = facets(session).people[0]
    assert {r.document.id for r in rows(session, Filters(person=maria.id))} == {a.id, b.id}
    assert [r.document.id for r in rows(session, Filters(year=2024))] == [a.id]
    assert [r.document.id for r in rows(session, Filters(kind="test"))] == [b.id]
    eyes = next(x for x in facets(session).areas if x.name == "Eyes")
    assert [r.document.id for r in rows(session, Filters(area=eyes.id))] == [b.id]
    stroke = next(x for x in facets(session).conditions if x.name == "Stroke") if facets(session).conditions else None
    assert stroke is None or [r.document.id for r in rows(session, Filters(condition=stroke.id))] == [a.id]


def test_unsorted_means_no_approved_area_and_check_means_a_doubtful_value(session, world):
    a, b, c = world
    assert [r.document.id for r in rows(session, Filters(unsorted=True))] == [c.id]
    assert [r.document.id for r in rows(session, Filters(check=True))] == [c.id]
    f = facets(session)
    assert f.unsorted == 1 and f.needs_check == 1 and f.unconfirmed_people == 2


def test_proposed_areas_show_on_the_row_but_do_not_count_as_sorting(session):
    doc = _paper(session, "Ana Lima", date(2026, 1, 1), "test", ["Teeth"])
    r = rows(session, Filters())[0]
    assert [x.name for x in r.proposed] == ["Teeth"] and r.areas == []
    assert [x.document.id for x in rows(session, Filters(unsorted=True))] == [doc.id]


def test_free_text_search_looks_at_the_provider_summary_and_file_name(session, world):
    assert len(rows(session, Filters(q="b.pdf"))) == 1


def test_a_paper_that_could_not_be_read_is_still_listed(session):
    from app.models.document import DocumentStatus
    make_document(session, domain=Domain.HEALTH, category="lab_result", status=DocumentStatus.NEEDS_ATTENTION, filename="bad.pdf")
    r = rows(session, Filters())
    assert len(r) == 1 and r[0].entry is None


def test_parse_filters_ignores_garbage():
    f = parse_filters({"person": "x", "year": "2026", "unsorted": "1", "kind": "test", "q": " eyes "})
    assert f.person is None and f.year == 2026 and f.unsorted and f.kind == "test" and f.q == "eyes"
