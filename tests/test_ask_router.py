import json

import pytest
from sqlmodel import select

from app.models.ask import AskConversation, AskStatus, AskTurn
from app.routers.ask import get_ask_client
from tests.fake_anthropic import FakeAnthropic, text_response, tool_response
from tests.knowledge_factories import make_claim, make_conversation, make_page, make_turn


@pytest.fixture()
def fake_client(client):
    from app.main import app
    def _set(responses):
        fake = FakeAnthropic(responses)
        app.dependency_overrides[get_ask_client] = lambda: fake
        return fake
    yield _set
    app.dependency_overrides.pop(get_ask_client, None)


def test_home_shows_new_chat_and_recent_conversations(client, session):
    conv = make_conversation(session, "Boiler questions", started_by="rute@example.com")
    make_turn(session, conv, "q")
    r = client.get("/ask")
    assert r.status_code == 200 and 'href="/ask"' in r.text
    assert "New chat" in r.text and "Boiler questions" in r.text and "rute@example.com" in r.text
    assert f'href="/ask/c/{conv.id}"' in r.text


def test_home_shows_a_friendly_datetime_for_the_last_active_conversation(client, session):
    from datetime import datetime

    conv = make_conversation(session, "Boiler questions")
    make_turn(session, conv, "q")
    conv.updated_at = datetime(2026, 8, 18, 9, 5, 52, 222395)
    session.add(conv)
    session.commit()

    r = client.get("/ask")

    assert "18 Aug 2026, 09:05" in r.text
    assert "09:05:52.222395" not in r.text


def test_new_chat_answers_in_background_and_redirects_to_conversation(client, session, fake_client):
    page = make_page(session, "Boiler", summary="Vaillant")
    make_claim(session, page, "last_service", "2026-03-02")
    fake_client([tool_response(("read_wiki_pages", {"page_ids": [page.id]})),
                 text_response(f"2 March 2026 [[wiki:{page.id}]].")])

    r = client.post("/ask", data={"question": "When was the boiler serviced?"}, follow_redirects=False)

    conv = session.exec(select(AskConversation)).one()
    assert r.status_code == 303 and r.headers["location"] == f"/ask/c/{conv.id}"
    chat = client.get(f"/ask/c/{conv.id}")
    assert "2 March 2026" in chat.text and f'href="/wiki/{page.id}"' in chat.text and "Save to wiki" in chat.text
    assert 'hx-post="/ask/c/' in chat.text  # follow-up box


def test_htmx_new_chat_uses_hx_redirect(client, session, fake_client):
    fake_client([text_response("I don't know.")])
    r = client.post("/ask", data={"question": "Anything?"}, headers={"HX-Request": "true"})
    conv = session.exec(select(AskConversation)).one()
    assert r.headers["HX-Redirect"] == f"/ask/c/{conv.id}"


def test_follow_up_appends_turn_and_uses_history(client, session, fake_client):
    page = make_page(session, "Boiler")
    conv = make_conversation(session, "Boiler")
    make_turn(session, conv, "When was the boiler serviced?", answer_text=f"March [[wiki:{page.id}]].",
              citations=[{"ref": f"wiki:{page.id}", "label": "Wiki: Boiler", "url": f"/wiki/{page.id}"}])
    fake = fake_client([text_response(f"Same visit [[wiki:{page.id}]].")])

    r = client.post(f"/ask/c/{conv.id}/turns", data={"question": "and the dishwasher?"},
                    headers={"HX-Request": "true"})

    assert r.status_code == 200 and "and the dishwasher?" in r.text
    turns = session.exec(select(AskTurn).where(AskTurn.conversation_id == conv.id).order_by(AskTurn.position)).all()
    assert [t.position for t in turns] == [1, 2]
    assert [m["role"] for m in fake.messages.calls[0]["messages"]] == ["user", "assistant", "user"]


def test_follow_up_while_pending_is_409(client, session):
    conv = make_conversation(session)
    make_turn(session, conv, "q", status=AskStatus.PENDING)
    assert client.post(f"/ask/c/{conv.id}/turns", data={"question": "again"}).status_code == 409


def test_empty_question_rejected_and_unknown_conversation_404(client):
    assert client.post("/ask", data={"question": "   "}).status_code == 400
    assert client.get("/ask/c/999").status_code == 404


def test_pending_turn_partial_keeps_polling(client, session):
    conv = make_conversation(session)
    turn = make_turn(session, conv, "q", status=AskStatus.PENDING)
    assert f'hx-get="/ask/turns/{turn.id}"' in client.get(f"/ask/turns/{turn.id}").text


def test_save_turn_redirects_to_new_wiki_page(client, session):
    page = make_page(session, "Boiler")
    conv = make_conversation(session)
    turn = make_turn(session, conv, "q", answer_text=f"x [[wiki:{page.id}]]",
                     citations=[{"ref": f"wiki:{page.id}", "label": "Wiki: Boiler", "url": f"/wiki/{page.id}"}])
    r = client.post(f"/ask/turns/{turn.id}/save", data={"title": "Boiler answer"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/wiki/")
