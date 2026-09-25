import pytest
from sqlmodel import select

from app.models.ask import AskStatus
from app.models.domain import Domain
from app.models.wiki import ANSWER_PAGE_TYPE, WikiLink, WikiLogEntry, WikiOperation
from app.services.ask.save import AnswerNotSaveable, save_answer_as_wiki_page
from app.services.wiki_store import active_claims
from tests.knowledge_factories import make_conversation, make_document, make_page, make_turn


def test_saves_one_turn_with_claims_links_and_query_log(session, fake_domain):
    boiler = make_page(session, "Boiler")
    doc = make_document(session, filename="service.pdf")
    conv = make_conversation(session)
    turn = make_turn(session, conv, "When was the boiler last serviced?",
                     answer_text=f"On 2 March 2026 [[wiki:{boiler.id}]][[doc:{doc.id}]].", citations=[
                         {"ref": f"wiki:{boiler.id}", "label": "Wiki: Boiler", "url": f"/wiki/{boiler.id}"},
                         {"ref": f"doc:{doc.id}", "label": "Fake document: service.pdf", "url": "/x"}])

    page = save_answer_as_wiki_page(session, turn.id, "Boiler last service")

    assert page.page_type == ANSWER_PAGE_TYPE and page.domain == Domain.HOUSE and page.topic == "Boiler last service"
    claims = {c.key: c.value for c in active_claims(session, page.id)}
    assert claims["answer"] == "On 2 March 2026 (Wiki: Boiler) (Fake document: service.pdf)."
    assert claims["question"] == turn.question and f"doc:{doc.id}" in claims["sources"]
    assert session.exec(select(WikiLink).where(WikiLink.from_page_id == page.id)).one().to_page_id == boiler.id
    assert session.exec(select(WikiLogEntry).where(WikiLogEntry.operation == WikiOperation.QUERY)).first()
    session.refresh(turn)
    assert turn.saved_wiki_page_id == page.id
    assert save_answer_as_wiki_page(session, turn.id, "again").id == page.id


def test_follow_up_question_claim_carries_previous_question(session, fake_domain):
    page = make_page(session, "Dishwasher")
    conv = make_conversation(session)
    make_turn(session, conv, "When was the boiler serviced?", answer_text="March.")
    follow = make_turn(session, conv, "and the dishwasher?", answer_text=f"June [[wiki:{page.id}]].",
                       citations=[{"ref": f"wiki:{page.id}", "label": "Wiki: Dishwasher", "url": f"/wiki/{page.id}"}])
    saved = save_answer_as_wiki_page(session, follow.id, "Dishwasher service")
    question = {c.key: c.value for c in active_claims(session, saved.id)}["question"]
    assert question == "When was the boiler serviced? → and the dishwasher?"


def test_title_of_a_topic_page_gets_suffix_and_mixed_domains_are_general(session, fake_domain):
    make_page(session, "Boiler")
    fin = make_page(session, "Electricity", domain=Domain.FINANCIALS)
    house = make_page(session, "Boiler room")
    conv = make_conversation(session)
    turn = make_turn(session, conv, "q", answer_text=f"x [[wiki:{fin.id}]] [[wiki:{house.id}]]", citations=[
        {"ref": f"wiki:{fin.id}", "label": "a", "url": "/a"}, {"ref": f"wiki:{house.id}", "label": "b", "url": "/b"}])
    page = save_answer_as_wiki_page(session, turn.id, "Boiler")
    assert page.topic == "Boiler (answer)" and page.domain is None


def test_failed_turn_cannot_be_saved(session):
    conv = make_conversation(session)
    turn = make_turn(session, conv, "q", status=AskStatus.FAILED)
    with pytest.raises(AnswerNotSaveable):
        save_answer_as_wiki_page(session, turn.id, "t")
