"""PLAN step 3a: one review queue, "Waiting for you". One count in the menu, filters by area, and every decision
stamps who and when. First producer: step 2's AI answers saved switched off below the confidence bar."""

from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.models.review import APPROVED, DISCARDED, SNOOZED, ReviewItem
from app.models.rule import AI, PERSON, Rule
from app.models.transaction import Transaction
from app.services import review
from app.services.taxonomy import UNSORTED_SLUG, get_node
from tests.test_rules import REST, _classify_new


def _doubtful(session):
    t, rule = _classify_new(session, "LOJA NOVA LDA", 0.55)
    return t, rule


def test_a_doubtful_ai_answer_waits_for_a_person_with_what_and_why(session, levels_not_binding):
    _, rule = _doubtful(session)
    [item] = review.waiting(session)
    assert (item.area, item.kind, item.ref) == ("money", "ai_rule", f"rule:{rule.id}")
    assert "Loja Nova" in item.title and "55% sure" in item.why
    assert review.count(session) == 1 and review.area_counts(session) == {"money": 1}
    review.waiting(session)
    assert len(session.exec(select(ReviewItem)).all()) == 1                       # reading twice adds nothing


def test_approving_files_the_entries_switches_the_rule_on_and_remembers_it_as_a_persons(session, levels_not_binding):
    t, rule = _doubtful(session)
    [item] = review.waiting(session)
    done = review.decide(session, item.id, review.APPROVE, by="rute@example.com", remember=True)
    assert (done.status, done.decided_by, done.remembered) == (APPROVED, "rute@example.com", True)
    assert done.decided_at is not None
    session.refresh(t); session.refresh(rule)
    assert rule.enabled and t.category_id == get_node(session, REST).id
    person = session.exec(select(Rule).where(Rule.source == PERSON)).one()
    assert (person.pattern, person.approved_by) == ("loja nova", "rute@example.com")
    assert review.count(session) == 0


def test_no_leaves_everything_as_it_was(session, levels_not_binding):
    t, rule = _doubtful(session)
    [item] = review.waiting(session)
    assert review.decide(session, item.id, review.DISCARD, by="p@x").status == DISCARDED
    session.refresh(t); session.refresh(rule)
    assert not rule.enabled and t.category_id == get_node(session, UNSORTED_SLUG).id
    assert review.waiting(session) == []                                          # a "no" is not asked again


def test_ask_me_in_a_week_puts_it_away_for_seven_days(session, levels_not_binding):
    _doubtful(session)
    [item] = review.waiting(session)
    assert review.decide(session, item.id, review.SNOOZE, by="p@x").status == SNOOZED
    assert review.waiting(session) == []
    assert [i.id for i in review.waiting(session, now=datetime.utcnow() + timedelta(days=7, minutes=1))] == [item.id]


def test_the_page_shows_one_count_in_the_menu_filters_by_area_and_refuses_a_second_answer(client, engine, levels_not_binding,
                                                                                          monkeypatch):
    monkeypatch.setattr("app.db.engine", engine)           # the menu count opens its own session on the app's engine
    with Session(engine) as session:
        _doubtful(session)
        item_id = review.waiting(session)[0].id
    home = client.get("/").text
    assert 'href="/waiting">Waiting for you (1)</a>' in home
    page = client.get("/waiting").text
    assert "Loja Nova" in page and "Money (1)" in page and "Ask me in a week" in page
    assert "Nothing is waiting for you in Health" in client.get("/waiting?area=health").text
    first = client.post(f"/waiting/{item_id}", data={"answer": "approve", "remember": "1"})
    assert first.status_code == 200 and "remembered for next time" in first.text
    again = client.post(f"/waiting/{item_id}", data={"answer": "approve"})
    assert again.status_code == 409 and "already answered" in again.text and "Reload the page" in again.text


def test_the_count_is_right_the_moment_the_engine_saves_a_doubtful_answer(session, levels_not_binding):
    _doubtful(session)
    assert review.count(session) == 1                     # no page opened: the engine opened the item itself
