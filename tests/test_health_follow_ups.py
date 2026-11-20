"""PLAN step 7b: follow-ups and reminders. A follow-up becomes a Health To-Do two weeks before it is due (overdue in
red on Health), a paper's own words propose one, and its reminder reaches the phone once without clinical words."""

from datetime import date, datetime, timedelta

from sqlmodel import select

from app.categories.health import conditions as cs, follow_ups as fu
from app.categories.health.extraction import ExtractedHealth
from app.categories.health.store import apply_extraction, entry_for
from app.models.category import Category
from app.models.paper_text import PDF_TEXT, PaperText
from app.models.review import ReviewItem
from app.models.todo import Todo
from app.services import notices, review
from tests.knowledge_factories import make_document


def _paper(session, text="", when=date(2026, 3, 2), person="Pedro Almeida"):
    doc = make_document(session, category=Category.HEALTH, paper_kind="follow_up")
    if text:
        session.add(PaperText(document_id=doc.id, text=text, method=PDF_TEXT))
        session.commit()
    apply_extraction(session, doc, ExtractedHealth(person_name=person, document_date=when, provider="Neuro",
                                                   record_kind="follow_up", summary="s", layout="report"))
    return doc, entry_for(session, doc.id)


def test_a_follow_up_is_a_health_todo_two_weeks_before(session):
    _, e = _paper(session)
    condition = cs.create(session, e.person_id, "Ischaemic stroke", "pedro@x")
    f = fu.add(session, 0, "Repeat the MRI", date(2027, 3, 2), "pedro@x", condition_id=condition.id)
    todo = session.get(Todo, f.todo_id)
    assert (todo.category, todo.due_date) == (Category.HEALTH, date(2027, 2, 16))
    assert "Repeat the MRI" in todo.title and "stroke" not in todo.title.lower()     # the To-Do list is family-open
    [nxt] = fu.open_ones(session, date(2027, 2, 1), condition_id=condition.id)
    assert nxt.note == "in 29 days"
    todo.done = True
    session.add(todo)
    session.commit()
    assert fu.open_ones(session, date(2027, 2, 1)) == []                             # done with its To-Do


def test_a_papers_words_propose_a_follow_up_dated_from_the_paper(session):
    _, e = _paper(session, text="Conclusão: estável. Repetir RM crânio-encefálica em 12 meses.")
    [p] = fu.proposals(session)
    assert (p.due_on, p.source, p.state) == (date(2027, 3, 2), "paper", "proposed")
    assert p.source_text == "Repetir RM crânio-encefálica em 12 meses"
    assert session.exec(select(Todo)).all() == []                                    # nothing until a yes
    review.sync(session)
    [item] = session.exec(select(ReviewItem).where(ReviewItem.kind == "health_follow_up")).all()
    review.decide(session, item.id, review.APPROVE, by="rute@x")
    [todo] = session.exec(select(Todo)).all()
    assert todo.due_date == date(2027, 2, 16)


def test_english_and_written_numbers_and_no_double_proposal(session):
    doc, e = _paper(session, text="Review in one year. Follow-up within 6 weeks.", when=date(2026, 1, 10))
    assert sorted(p.due_on for p in fu.proposals(session)) == [date(2026, 2, 21), date(2027, 1, 10)]
    fu.propose_from_paper(session, e)                                                # read again: not twice
    assert len(fu.proposals(session)) == 2


def test_saying_no_is_kept(session):
    _, e = _paper(session, text="Controlo dentro de 6 meses.")
    [p] = fu.proposals(session)
    fu.dismiss(session, p.id)
    fu.propose_from_paper(session, e)
    assert fu.proposals(session) == []


def test_the_reminder_reaches_the_phone_once_without_clinical_words(session):
    _, e = _paper(session)
    f = fu.add(session, e.person_id, "Repeat the MRI", date(2026, 12, 1), "pedro@x")
    assert [x for x in notices.current(session, date(2026, 11, 16)) if x.kind == notices.FOLLOW_UP_DUE] == []
    [found] = [x for x in notices.current(session, date(2026, 11, 17)) if x.kind == notices.FOLLOW_UP_DUE]
    assert found.text == "A Health follow-up for Pedro is due on 2026-12-01: see To-Dos."
    assert "MRI" not in found.text
    plan = notices.refresh(session, datetime(2026, 11, 17, 10, 0))
    notices.mark_sent(session, plan, datetime(2026, 11, 17, 10, 0))
    again = notices.refresh(session, datetime(2026, 11, 25, 10, 0))
    assert not [n for n in again.new + again.reminders if n.kind == notices.FOLLOW_UP_DUE]


def test_the_condition_page_adds_and_shows_the_next_due(client, session):
    _, e = _paper(session)
    condition = cs.create(session, e.person_id, "Migraine", "pedro@x")
    due = (date.today() + timedelta(days=40)).isoformat()
    r = client.post(f"/health/conditions/{condition.id}/follow-ups", data={"what": "Neurology visit", "due_on": due},
                    follow_redirects=False)
    assert r.status_code == 303
    page = client.get(f"/health/conditions/{condition.id}").text
    assert "Next due" in page and "Neurology visit" in page and due in page
    bad = client.post(f"/health/conditions/{condition.id}/follow-ups", data={"what": "", "due_on": ""},
                      headers={"accept": "text/html"})
    assert bad.status_code == 400 and "A follow-up needs what to do" in bad.text
