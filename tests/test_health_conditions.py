"""PLAN step 7a: conditions are records of their own per person; the Hub suggests which papers are about one and a
person confirms, picks another or says none; a condition the reader named is proposed, created only on a yes."""

from datetime import date

from sqlmodel import select

from app.categories.health import conditions as cs
from app.categories.health.extraction import ExtractedHealth
from app.categories.health.store import apply_extraction, entry_for, purge_derived
from app.models.category import Category
from app.models.health import HealthEntryCondition
from app.models.review import ReviewItem
from app.models.subject import Subject, SubjectKind
from app.services import review
from tests.knowledge_factories import make_document


def _paper(session, person="Pedro Almeida", conditions=(), when=date(2026, 3, 2)):
    doc = make_document(session, category=Category.HEALTH, paper_kind="follow_up")
    apply_extraction(session, doc, ExtractedHealth(person_name=person, document_date=when, provider="Neuro",
                                                   record_kind="follow_up", summary="s", layout="report",
                                                   conditions=list(conditions)))
    return doc, entry_for(session, doc.id)


def test_a_named_condition_is_proposed_and_created_only_on_a_yes(session):
    _, e1 = _paper(session, conditions=["AVC isquémico"])
    _, e2 = _paper(session, conditions=["AVC isquémico"], when=date(2026, 6, 1))
    [p] = cs.proposals(session)
    assert (p.person.id, p.name, sorted(p.papers)) == (e1.person_id, "AVC isquémico", sorted([e1.id, e2.id]))
    assert cs.conditions_of(session, e1.person_id) == []                     # nothing is created on its own
    condition = cs.accept_proposal(session, p.person.id, p.area_id, "pedro@x")
    assert [e.id for e in cs.papers_of(session, condition.id)] == [e2.id, e1.id]
    assert cs.proposals(session) == []
    subject = session.get(Subject, condition.subject_id)
    assert subject.kind == SubjectKind.CONDITION                              # a condition is a subject (step 6)


def test_a_later_paper_is_suggested_and_a_person_decides(session):
    _, e1 = _paper(session)
    condition = cs.create(session, e1.person_id, "Ischaemic stroke", "pedro@x")
    _, e2 = _paper(session, conditions=["AVC"], when=date(2026, 9, 1))      # another wording of the same condition
    box = cs.entry_box(session, e2)
    assert [c.id for c in box.suggested] == [condition.id] and box.proposed == []
    cs.decide(session, e2.id, condition.id, cs.CONFIRMED, "rute@x")
    link = session.get(HealthEntryCondition, (e2.id, condition.id))
    assert (link.state, link.decided_by) == ("confirmed", "rute@x")


def test_none_of_these_rejects_the_suggestions_and_a_reread_keeps_the_decision(session):
    _, e1 = _paper(session)
    condition = cs.create(session, e1.person_id, "Diabetes", "pedro@x")
    doc, e2 = _paper(session, conditions=["Diabetes mellitus"])
    cs.none_of_these(session, e2.id, "pedro@x")
    assert cs.entry_box(session, e2).suggested == []
    cs.suggest_for_entry(session, e2)                                          # read again: not suggested again
    assert session.get(HealthEntryCondition, (e2.id, condition.id)).state == "rejected"


def test_a_paper_of_someone_else_cannot_be_put_on_a_condition(session):
    _, e1 = _paper(session)
    _, other = _paper(session, person="Rute Correia")
    condition = cs.create(session, e1.person_id, "Asthma", "pedro@x")
    try:
        cs.decide(session, other.id, condition.id, cs.CONFIRMED, "pedro@x")
        assert False, "refused"
    except cs.ConditionError as exc:
        assert exc.problem.what == "Not saved." and "Pick one of this person's conditions" in exc.problem.next_step


def test_the_queue_asks_about_proposals_and_suggestions(session):
    _, e1 = _paper(session, conditions=["Placa ateromatosa"])
    review.sync(session)
    [item] = session.exec(select(ReviewItem).where(ReviewItem.kind == "health_condition_new")).all()
    assert item.title == "A condition for Pedro Almeida? Placa ateromatosa"
    review.decide(session, item.id, review.APPROVE, by="pedro@x")
    [condition] = cs.conditions_of(session, e1.person_id)
    assert [e.id for e in cs.papers_of(session, condition.id)] == [e1.id]
    _, e2 = _paper(session, conditions=["atheromatous plaque"], when=date(2026, 10, 1))
    review.sync(session)
    [ask] = session.exec(select(ReviewItem).where(ReviewItem.kind == "health_condition_paper",
                                                  ReviewItem.status.in_(review.OPEN))).all()
    review.decide(session, ask.id, review.DISCARD, by="pedro@x")
    assert session.get(HealthEntryCondition, (e2.id, condition.id)).state == "rejected"
    assert not session.exec(select(ReviewItem).where(ReviewItem.kind == "health_area",
                                                     ReviewItem.title.contains("Placa"))).all()


def test_the_paper_box_and_the_condition_page(client, session):
    doc, e1 = _paper(session, conditions=["Migraine"])
    r = client.post(f"/health/documents/{doc.id}/conditions", data={"action": "create", "name": "Migraine"},
                    follow_redirects=False)
    assert r.status_code == 303
    [condition] = cs.conditions_of(session, e1.person_id)
    page = client.get(f"/health/conditions/{condition.id}").text
    assert "Migraine" in page and f"/health/documents/{doc.id}" in page
    again = client.post(f"/health/documents/{doc.id}/conditions", data={"action": "create", "name": "migraine"},
                        headers={"accept": "text/html"})
    assert again.status_code == 409 and "already has the condition Migraine" in again.text
    detail = client.get(f"/health/documents/{doc.id}").text
    assert 'id="conditions"' in detail and "Migraine" in detail


def test_a_discarded_paper_takes_its_links_with_it(session):
    doc, e1 = _paper(session, conditions=["Asthma"])
    condition = cs.create(session, e1.person_id, "Asthma", "pedro@x", confirm_entry_id=e1.id)
    purge_derived(session, doc)
    assert cs.papers_of(session, condition.id) == []
