import pytest

from app.models.ask import AskStatus
from app.services.ask import conversation as conv_service
from tests.knowledge_factories import make_conversation, make_turn


def test_start_conversation_creates_titled_conversation_and_first_turn(session):
    turn = conv_service.start_conversation(session, "  When was the boiler serviced?  ", started_by="rute@example.com")
    conv = session.get(conv_service.AskConversation, turn.conversation_id)
    assert conv.title == "When was the boiler serviced?" and conv.started_by == "rute@example.com"
    assert turn.position == 1 and turn.status == AskStatus.PENDING and turn.asked_by == "rute@example.com"


def test_add_turn_appends_and_refuses_while_pending(session):
    first = conv_service.start_conversation(session, "q1", started_by=None)
    with pytest.raises(conv_service.TurnInProgress):
        conv_service.add_turn(session, first.conversation_id, "q2", asked_by=None)
    first.status = AskStatus.ANSWERED
    session.add(first)
    session.commit()
    second = conv_service.add_turn(session, first.conversation_id, "and the dishwasher?", asked_by="matias@example.com")
    assert second.position == 2
    assert [t.question for t in conv_service.turns_of(session, first.conversation_id)] == ["q1", "and the dishwasher?"]


def test_history_replays_answered_turns_with_sources_and_known_refs(session):
    conv = make_conversation(session)
    make_turn(session, conv, "When was the boiler serviced?", answer_text="2 March 2026 [[wiki:1]].",
              citations=[{"ref": "wiki:1", "label": "Wiki: Boiler", "url": "/wiki/1"}])
    make_turn(session, conv, "broken", status=AskStatus.FAILED)
    current = make_turn(session, conv, "and the dishwasher?", status=AskStatus.PENDING)

    messages, known = conv_service.build_history(session, current)

    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "When was the boiler serviced?"
    assert "[[wiki:1]]" in messages[1]["content"] and "Sources cited: wiki:1 = Wiki: Boiler" in messages[1]["content"]
    assert set(known) == {"wiki:1"}


def test_history_is_bounded_and_notes_omitted_turns(session):
    conv = make_conversation(session)
    for i in range(1, 9):
        make_turn(session, conv, f"q{i}", answer_text="x" * 5000 if i == 8 else f"a{i}")
    current = make_turn(session, conv, "q9", status=AskStatus.PENDING)

    messages, _ = conv_service.build_history(session, current)

    assert len(messages) == 2 * conv_service.MAX_HISTORY_TURNS
    assert messages[0]["content"].startswith("(2 earlier question(s) in this chat are not shown.)")
    assert messages[0]["content"].endswith("q3")
    assert len(messages[-1]["content"]) < 4200 and messages[-1]["content"].count("x") == conv_service.MAX_HISTORY_ANSWER_CHARS


def test_recent_conversations_newest_first_with_counts(session):
    older = make_conversation(session, "Older")
    make_turn(session, older, "a")
    newer = conv_service.start_conversation(session, "Newer", started_by="vicente@example.com")
    summaries = conv_service.recent_conversations(session)
    assert [s.conversation.title for s in summaries] == ["Newer", "Older"]
    assert [s.turn_count for s in summaries] == [1, 1]
    assert summaries[0].conversation.id == newer.conversation_id
