"""PLAN step 10f: Health, new look. A tile per person (no faces), the follow-ups across the family, papers by area; a
person's page with conditions, results against the printed reference range, and a timeline. Same data as the records
list and the results page."""

import re
from datetime import date, timedelta

from sqlmodel import select

from app.categories.health import areas as areas_svc
from app.categories.health.kinds import KIND_LABELS
from app.categories.health.extraction import ExtractedHealth, ExtractedMeasurement
from app.categories.health.store import apply_extraction, entry_for
from app.models.category import Category
from app.models.health import HealthArea
from app.services import health_page as hp
from app.services.todo_engine import upsert_source_todo
from app.ui.charts import MAX_EXAGGERATION, exaggeration, make_scale
from tests.knowledge_factories import make_document

TODAY = date(2026, 11, 20)


def _m(test, value, unit, low=None, high=None):
    return ExtractedMeasurement(test, value, None, unit, low, high, None, False, "", group_path="Hemograma",
                                reference_text=f"{low} - {high}" if low is not None else None)


def _paper(session, person, when, kind="test", measurements=(), areas=(), conditions=(), name=None):
    doc = make_document(session, category=Category.HEALTH, paper_kind="lab_result", filename=name or f"{person}-{when}.pdf")
    apply_extraction(session, doc, ExtractedHealth(person_name=person, document_date=when, provider="Cintramédica",
                                                   record_kind=kind, summary="s", layout="sheet",
                                                   measurements=list(measurements), areas=list(areas),
                                                   conditions=list(conditions)))
    return doc, entry_for(session, doc.id)


def _family(session):
    d1, rute = _paper(session, "Rute Almeida", date(2025, 11, 5), measurements=[_m("Hemoglobina", 12.1, "g/dL", 11.5, 15.5)],
                      areas=["Blood and lab"])
    _paper(session, "Rute Almeida", date(2026, 3, 2), measurements=[_m("Hemoglobina", 16.2, "g/dL", 11.5, 15.5)],
           areas=["Blood and lab"], conditions=["Seasonal allergy"])
    d3, child = _paper(session, "Vicente Almeida", date(2026, 6, 14), kind="vaccination")
    for a in session.exec(select(HealthArea)).all():
        areas_svc.approve_area(session, a.id)
    upsert_source_todo(session, d1, title="Repeat blood test", due_date=TODAY - timedelta(days=3), category=Category.HEALTH)
    upsert_source_todo(session, d3, title="Paediatric check-up", due_date=TODAY + timedelta(days=21), category=Category.HEALTH)
    session.commit()
    return rute.person_id, child.person_id


def test_a_tile_per_person_with_the_next_follow_up_and_the_latest_paper(session):
    rute, child = _family(session)
    h = hp.health_home(session, TODAY)
    tiles = {t.person.id: t for t in h.tiles}
    assert tiles[rute].papers == 2 and tiles[rute].latest == f"{KIND_LABELS['test']} 2026-03-02"
    assert tiles[rute].next.what == "Repeat blood test" and tiles[child].next.what == "Paediatric check-up"
    assert [(f.what, f.note, f.late) for f in h.follow_ups] == [("Repeat blood test", "overdue", True),
                                                                ("Paediatric check-up", "in 21 days", False)]
    assert {(a.name, a.papers) for a in h.areas} == {("Blood and lab", 2), ("Seasonal allergy", 1)}


def test_the_health_page_shows_names_not_faces(client, session):
    _family(session)
    html = client.get("/health/family").text
    assert "Rute Almeida" in html and "Vicente Almeida" in html and "Follow-ups across the family" in html
    assert "<img" not in html.split('<main', 1)[-1]                       # no faces, no avatars
    assert 'class="chip bad">overdue' in html and 'href="/health/records?area=' in html


def test_a_persons_page_draws_results_against_the_reference_range(client, session):
    rute, _ = _family(session)
    p = hp.person_page(session, rute, TODAY)
    [line] = p.results
    assert line.ref == (11.5, 15.5) and [i.value for i in line.items] == [12.1, 16.2] and line.items[-1].current
    values = [i.value for i in line.items]
    assert exaggeration(make_scale(values), values) <= MAX_EXAGGERATION + 1e-9
    assert [c.name for c in p.conditions] == ["Seasonal allergy"]
    assert [t.when for t in p.timeline] == [date(2026, 3, 2), date(2025, 11, 5)]     # Rute's follow-up is overdue
    html = client.get(f"/health/people/{rute}").text
    svg = " ".join(re.findall(r"<svg.*?</svg>", html, re.S))
    assert "reference range 11.5 to 15.5" in svg and "Every result ›" in html


def test_a_follow_up_ahead_is_on_the_timeline(session):
    _, child = _family(session)
    p = hp.person_page(session, child, TODAY)
    assert p.timeline[0].ahead and p.timeline[0].what == "Paediatric check-up" and p.timeline[0].note == "in 21 days"


def test_an_unknown_person_is_refused_in_three_parts(client):
    r = client.get("/health/people/9999", headers={"accept": "text/html"})
    assert r.status_code == 404 and "That person is no longer listed" in r.text


def test_the_health_menu_opens_on_the_family_page(client):
    html = client.get("/health/family").text
    assert 'class="pill on" href="/health/family"' in html and 'href="/health/records"' in html
