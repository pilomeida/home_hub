"""Ask chat bookkeeping: conversations, ordered turns, and the bounded
history replayed to Claude so follow-ups build on earlier turns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, func, select

from app.models.ask import AskConversation, AskStatus, AskTurn
from app.services.ask.citations import known_from_turn
from app.services.ask.contracts import Citable

MAX_HISTORY_TURNS = 6
MAX_HISTORY_ANSWER_CHARS = 4000
_TITLE_CHARS = 120


class TurnInProgress(Exception):
    pass


@dataclass
class ConversationSummary:
    conversation: AskConversation
    turn_count: int


def _new_turn(session: Session, conv: AskConversation, position: int, question: str, asked_by) -> AskTurn:
    turn = AskTurn(conversation_id=conv.id, position=position, question=question, asked_by=asked_by)
    conv.updated_at = datetime.utcnow()
    session.add(turn)
    session.add(conv)
    session.commit()
    session.refresh(turn)
    return turn


def start_conversation(session: Session, question: str, started_by) -> AskTurn:
    question = question.strip()
    conv = AskConversation(title=question[:_TITLE_CHARS], started_by=started_by)
    session.add(conv)
    session.commit()
    session.refresh(conv)
    return _new_turn(session, conv, 1, question, started_by)


def turns_of(session: Session, conversation_id: int) -> list[AskTurn]:
    return list(session.exec(select(AskTurn).where(AskTurn.conversation_id == conversation_id)
                             .order_by(AskTurn.position)).all())


def add_turn(session: Session, conversation_id: int, question: str, asked_by) -> AskTurn:
    conv = session.get(AskConversation, conversation_id)
    if conv is None:
        raise KeyError(conversation_id)
    turns = turns_of(session, conversation_id)
    if any(t.status == AskStatus.PENDING for t in turns):
        raise TurnInProgress("The previous question is still being answered")
    return _new_turn(session, conv, (turns[-1].position if turns else 0) + 1, question.strip(), asked_by)


def build_history(session: Session, turn: AskTurn) -> tuple[list[dict], dict[str, Citable]]:
    """Earlier ANSWERED turns as plain user/assistant messages (oldest first),
    bounded to the last MAX_HISTORY_TURNS; their cited refs stay citable."""
    answered = [t for t in turns_of(session, turn.conversation_id)
                if t.position < turn.position and t.status == AskStatus.ANSWERED and t.answer_text]
    kept = answered[-MAX_HISTORY_TURNS:]
    omitted = len(answered) - len(kept)
    messages: list[dict] = []
    known: dict[str, Citable] = {}
    for i, past in enumerate(kept):
        cited = known_from_turn(past)
        known.update(cited)
        question = past.question
        if i == 0 and omitted:
            question = f"({omitted} earlier question(s) in this chat are not shown.)\n{question}"
        answer = past.answer_text[:MAX_HISTORY_ANSWER_CHARS]
        if cited:
            answer += "\n\nSources cited: " + "; ".join(f"{c.ref} = {c.label}" for c in cited.values())
        messages += [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
    return messages, known


def recent_conversations(session: Session, limit: int = 20) -> list[ConversationSummary]:
    convs = session.exec(select(AskConversation).order_by(AskConversation.updated_at.desc(),
                                                          AskConversation.id.desc()).limit(limit)).all()
    counts = dict(session.exec(select(AskTurn.conversation_id, func.count()).where(
        AskTurn.conversation_id.in_([c.id for c in convs])).group_by(AskTurn.conversation_id)).all()) if convs else {}
    return [ConversationSummary(c, counts.get(c.id, 0)) for c in convs]
