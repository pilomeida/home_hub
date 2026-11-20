"""PLAN step 7c: visits (booked, then done), notes (note, own reading, episode), medication and its changes, and
one timeline per condition. Done when (with 7b): a condition page shows its papers, visits, medication and next
follow-up."""

from datetime import date, timedelta

from app.categories.health import care, conditions as cs, follow_ups as fu
from app.categories.health.extraction import ExtractedHealth
from app.categories.health.store import apply_extraction, entry_for
from app.models.category import Category
from tests.knowledge_factories import make_document


def _condition(session):
    doc = make_document(session, category=Category.HEALTH, paper_kind="follow_up")
    apply_extraction(session, doc, ExtractedHealth(person_name="Pedro Almeida", document_date=date(2026, 3, 2),
                                                   provider="Neuro", record_kind="follow_up", summary="s",
                                                   layout="report"))
    entry = entry_for(session, doc.id)
    return cs.create(session, entry.person_id, "Ischaemic stroke", "pedro@x", confirm_entry_id=entry.id), doc


def test_a_booked_visit_done_books_the_next(session):
    c, _ = _condition(session)
    v = care.add_visit(session, c.id, visit_on=date(2026, 5, 4), clinic="CUF", doctor="Dr. Silva", by="pedro@x")
    care.visit_done(session, v.id, observations="Stable", plan_changes="Lower the dose",
                    next_appointment_on=date(2026, 11, 4))
    assert [(x.status, x.visit_on) for x in care.visits(session, c.id)] == [("done", date(2026, 5, 4)),
                                                                           ("booked", date(2026, 11, 4))]


def test_medication_changes_are_kept_not_overwritten(session):
    c, _ = _condition(session)
    med = care.add_medication(session, c.id, name="Clopidogrel", dose="75 mg", frequency="daily",
                              started_on=date(2026, 3, 2), by="pedro@x")
    care.change_medication(session, med.id, changed_on=date(2026, 5, 4), dose="37.5 mg")
    care.change_medication(session, med.id, changed_on=date(2026, 9, 1), stop=True)
    titles = [(e.when, e.title, e.detail) for e in care.timeline(session, c.id, date(2026, 10, 1))
              if e.kind == "medication"]
    assert titles == [(date(2026, 9, 1), "Stopped Clopidogrel", ""),
                      (date(2026, 5, 4), "Changed Clopidogrel", "37.5 mg daily"),
                      (date(2026, 3, 2), "Started Clopidogrel", "75 mg daily")]
    try:
        care.change_medication(session, med.id, changed_on=date(2026, 9, 2), dose="10 mg")
        assert False
    except care.CareError as exc:
        assert "already stopped" in exc.problem.why


def test_a_reading_needs_its_value(session):
    c, _ = _condition(session)
    try:
        care.add_note(session, c.id, noted_on=date(2026, 6, 1), kind="reading", text="")
        assert False
    except care.CareError as exc:
        assert exc.problem.why == "A reading needs its value."
    care.add_note(session, c.id, noted_on=date(2026, 6, 1), kind="reading", text="morning", value=132, unit="mmHg")
    [ev] = [e for e in care.timeline(session, c.id) if e.kind == "reading"]
    assert ev.detail == "132 mmHg morning"


def test_the_condition_page_shows_papers_visits_medication_and_next_follow_up(client, session):
    c, doc = _condition(session)
    care.add_medication(session, c.id, name="Atorvastatin", dose="20 mg", started_on=date(2026, 3, 2))
    care.add_visit(session, c.id, visit_on=date.today() + timedelta(days=10), clinic="CUF")
    fu.add(session, 0, "Repeat the MRI", date.today() + timedelta(days=60), "pedro@x", condition_id=c.id)
    page = client.get(f"/health/conditions/{c.id}").text
    assert f"/health/documents/{doc.id}" in page and "Atorvastatin" in page and "Visit booked" in page
    assert "Next due" in page and "Repeat the MRI" in page and "Timeline" in page
    r = client.post(f"/health/conditions/{c.id}/notes", data={"noted_on": "", "kind": "note", "text": "x"},
                    headers={"accept": "text/html"})
    assert r.status_code == 400 and "The date is missing." in r.text


def test_the_overview_card_counts_conditions_and_the_next_follow_up_without_names(session):
    from app.categories.health.overview import health_overview_card
    c, _ = _condition(session)
    fu.add(session, 0, "Repeat the MRI", date(2027, 3, 2), "pedro@x", condition_id=c.id)
    texts = [l.text for l in health_overview_card(session, date(2026, 11, 20)).lines]
    assert "1 condition followed" in texts and "next follow-up 2027-03-02" in texts
    assert not any("stroke" in t.lower() or "MRI" in t for t in texts)
    late = [l for l in health_overview_card(session, date(2027, 3, 5)).lines if "overdue" in l.text]
    assert late and late[0].attention
