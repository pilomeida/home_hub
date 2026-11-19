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
    [left] = review.waiting(session)                                              # the AI's guess is not asked again;
    assert left.kind == "merchant" and "which category?" in left.title and left.ref != item.ref   # the merchant still needs one


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


# --- b. the other review points, moved onto the one queue -------------------------------------------------------

def _held_line(session):
    from app.models.document import Document, DocumentSource
    doc = Document(filename="extrato.pdf", file_path="/tmp/extrato.pdf", content_hash="h-held",
                   source=DocumentSource.MANUAL)
    session.add(doc); session.commit()
    t = Transaction(document_id=doc.id, description="PINGO DOCE LISBOA", amount=23.4, transaction_type=None,
                    review_status="needs_check", review_reason="The amount was hard to read.")
    session.add(t); session.commit()
    return t


def test_a_held_bank_line_waits_here_and_is_answered_here(session):
    from app.models.transaction import TransactionType
    t = _held_line(session)
    t.transaction_type = TransactionType.DEBIT
    session.add(t); session.commit()
    [item] = review.waiting(session, "money")
    assert item.kind == "to_check_line" and "PINGO DOCE" in item.title and "hard to read" in item.why
    review.decide(session, item.id, review.APPROVE, by="rute@example.com")
    session.refresh(t)
    assert (t.review_status, t.checked_by) == ("confirmed", "rute@example.com")


def test_a_line_answered_on_the_to_check_page_leaves_this_list_with_that_answers_who(session):
    from app.models.transaction import TransactionType
    from app.services import to_check
    t = _held_line(session)
    t.transaction_type = TransactionType.DEBIT
    session.add(t); session.commit()
    [item] = review.waiting(session)
    to_check.reject(session, t.id, "pedro@example.com")
    assert review.waiting(session) == []
    session.refresh(item)
    assert (item.status, item.decided_by) == (DISCARDED, "pedro@example.com")


def test_an_unconfirmed_merchant_waits_here_and_yes_confirms_it(session):
    from app.models.merchant import Merchant
    from app.services.taxonomy import ensure_taxonomy
    ensure_taxonomy(session)
    m = Merchant(canonical_name="Café Central", normalized_key="cafe central",
                 default_category_id=get_node(session, REST).id)
    session.add(m); session.commit()
    [item] = review.waiting(session)
    assert item.kind == "merchant" and "Café Central" in item.title
    review.decide(session, item.id, review.APPROVE, by="p@x", remember=True)
    session.refresh(m)
    assert m.confirmed and session.exec(select(Rule).where(Rule.pattern == "café central")).one().source == PERSON


def test_health_proposals_and_same_person_wait_here_and_are_answered_through_their_own_rules(session):
    from app.models.health import HealthArea, HealthPerson
    from app.models.subject import Subject, SubjectKind, SubjectMatch
    session.add(HealthArea(name="Eyes", kind="area", match_key="eyes", approved=False))
    session.add(HealthPerson(name="Vicente Almeida", match_key="vicente almeida", confirmed=False))
    from app.services import subjects
    subjects.register_person(session, "Rute Almeida")
    subjects.register_person(session, "Rute Alexandra Almeida")
    session.commit()
    assert len(subjects.pending(session)) == 1                                     # registering asked, as in production
    items = {i.kind: i for i in review.waiting(session)}
    assert set(items) == {"health_area", "health_person", "same_person"}
    assert review.area_counts(session) == {"health": 2, "inbox": 1}
    review.decide(session, items["health_area"].id, review.APPROVE, by="p@x")
    review.decide(session, items["same_person"].id, review.DISCARD, by="rute@example.com")
    assert session.exec(select(HealthArea)).one().approved
    match = session.exec(select(SubjectMatch)).one()
    assert (match.decision, match.decided_by) == ("different", "rute@example.com")
    assert [i.kind for i in review.waiting(session)] == ["health_person"]
