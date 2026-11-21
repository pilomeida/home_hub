"""PLAN step 10g slice 3: a file attached to a question goes to the Inbox as a suggestion, exactly as "Add a paper"
(same checks, same recognition, same approval). Undo takes it back out the way the Inbox's Discard does."""

import pytest
from sqlmodel import select

from app.main import app
from app.models.ask import AskAction, AskStatus, AskTurn
from app.models.category import Category
from app.models.document import Document, DocumentStatus
from app.routers.ask import get_ask_gateway, get_ask_inbox_classifier
from app.services.ask.conversation import build_history
from app.services.category_classifier import CategorySuggestion
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.fakes.text_pdf import text_pdf_bytes

PDF = text_pdf_bytes(["AEG dishwasher invoice", "Total 649.00 EUR"])


@pytest.fixture
def wired(inbox_documents_dir):
    async def classify(path, context):
        return CategorySuggestion(Category.HOUSE, "house_appliance", 0.95, "an appliance invoice")
    gw = FakeGateway([gateway_text_result("Noted.")])
    app.dependency_overrides[get_ask_inbox_classifier] = lambda: classify
    app.dependency_overrides[get_ask_gateway] = lambda: gw
    yield gw
    app.dependency_overrides.pop(get_ask_inbox_classifier, None)
    app.dependency_overrides.pop(get_ask_gateway, None)


def _send(client, question="", name="aeg.pdf", content=PDF, url="/ask"):
    return client.post(url, data={"question": question}, files={"file": (name, content, "application/pdf")},
                       follow_redirects=False)


def test_a_file_alone_lands_in_the_inbox_without_asking_the_ai(client, session, wired):
    r = _send(client)
    assert r.status_code == 303 and wired.requests == []
    doc = session.exec(select(Document)).one()
    assert doc.status == DocumentStatus.PENDING_REVIEW and doc.filename == "aeg.pdf"
    turn = session.exec(select(AskTurn)).one()
    assert turn.status == AskStatus.ANSWERED and turn.answer_text is None
    page = client.get(r.headers["location"]).text
    assert "aeg.pdf sent to the Inbox as a suggestion. Not filed yet." in page
    assert f"/inbox/{doc.id}/card" in page and "Review it now" in page and "Undo" in page


def test_undo_takes_it_out_of_the_inbox_and_keeps_the_file(client, session, wired):
    _send(client)
    action = session.exec(select(AskAction)).one()
    assert client.post(f"/ask/actions/{action.id}/undo", headers={"HX-Request": "true"}).status_code == 200
    session.expire_all()
    doc = session.exec(select(Document)).one()
    assert doc.status == DocumentStatus.DISCARDED and session.get(AskAction, action.id).status == "undone"


def test_with_a_question_the_ai_answers_and_never_sees_the_unfiled_paper(client, session, wired):
    _send(client, question="Is the dishwasher still under warranty?")
    turn = session.exec(select(AskTurn)).one()
    assert turn.status == AskStatus.ANSWERED and turn.answer_text == "Noted."
    assert "aeg.pdf" not in str(wired.requests) and "AEG" not in str(wired.requests)
    assert session.exec(select(AskAction)).one().turn_id == turn.id


def test_a_paper_only_turn_is_not_replayed_as_history(client, session, wired):
    r = _send(client)
    conv_id = int(r.headers["location"].rsplit("/", 1)[1])
    from app.services.ask.conversation import add_turn
    later = add_turn(session, conv_id, "and the oven?", asked_by=None)
    history, _ = build_history(session, later)
    assert "aeg" not in str(history).lower()


def test_a_refused_file_says_why_and_leaves_nothing(client, session, wired):
    r = _send(client, name="notes.docx", content=b"PK\x03\x04")
    assert r.status_code == 400 and "only takes PDFs, photos and short videos" in r.text
    assert session.exec(select(Document)).all() == [] and session.exec(select(AskTurn)).all() == []


def test_the_same_paper_twice_is_pointed_to_not_added(client, session, wired):
    _send(client)
    r = _send(client, name="aeg-copy.pdf")
    page = client.get(r.headers["location"]).text
    assert "aeg.pdf is already in the Hub." in page and len(session.exec(select(Document)).all()) == 1
    second = session.exec(select(AskAction).order_by(AskAction.id.desc())).first()
    assert client.post(f"/ask/actions/{second.id}/undo").status_code == 409          # never someone else's paper
