"""Ask's private-run bookkeeping (decision D18): which answers ran private, and how the person is told."""

from __future__ import annotations

import json

from sqlmodel import Session, select

from app.models.ask import AskStatus, AskTurn
from app.models.document import Document
from app.models.record import Record
from app.models.todo import Todo
from app.models.transaction import Transaction
from app.models.wiki import WikiClaim
from app.privacy.ratchet import RatchetLog
from app.privacy.reach import Reach, reach_for
from app.privacy.tiers import Tier
from app.problems import Problem
from app.services.ask.conversation import turns_of


def turn_is_private(turn: AskTurn) -> bool:
    return RatchetLog.from_json(turn.escalation_json).private


def _cites_private_now(session: Session, reach: Reach, turn: AskTurn) -> bool:
    """Does this answer cite a source whose type is private NOW? The type may have been raised to High after
    the answer was given at a lower level; the answer text may repeat details of it. Fails CLOSED: a citation
    record of an unexpected shape, or any error while checking it, reads as private."""
    try:
        return _cites_private(session, reach, turn)
    except Exception:
        return True


def _cites_private(session: Session, reach: Reach, turn: AskTurn) -> bool:
    cited = json.loads(turn.citations_json or "[]")
    if not isinstance(cited, list):
        raise ValueError("citations are a JSON list")
    private_claims = None
    for item in cited:
        parts = str(item["ref"]).split(":")
        if parts[0] == "financials" and len(parts) == 3 and parts[1] == "txn":
            kind, raw = "txn", parts[2]                    # financials:txn:N
        elif len(parts) == 2:
            kind, raw = parts
        else:
            continue                                       # e.g. a spending aggregate: not one source
        if not raw.isdigit():
            continue
        ident = int(raw)
        if kind == "doc" and reach.tier_of_source(session.get(Document, ident)) == Tier.HIGH:
            return True
        if kind == "rec" and reach.tier_of_source(session.get(Record, ident)) == Tier.HIGH:
            return True
        if kind == "todo":
            todo = session.get(Todo, ident)
            if todo is not None and reach.tier_of_todo(session, todo) == Tier.HIGH:
                return True
        if kind == "txn":
            txn = session.get(Transaction, ident)
            if txn is not None and reach.tier_of_source(session.get(Document, txn.document_id)) == Tier.HIGH:
                return True
        if kind == "wiki":
            if private_claims is None:
                private_claims = reach.private_claim_ids(session)
            if private_claims and any(c in private_claims for c in
                                      session.exec(select(WikiClaim.id).where(WikiClaim.page_id == ident))):
                return True
    return False


def chat_is_private(session: Session, turn: AskTurn) -> bool:
    """Did an earlier answer in this chat use private records, or cite one that is private now? Then this
    answer's history carries them, so it must start High (the replayed answers are part of every call)."""
    earlier = [t for t in turns_of(session, turn.conversation_id) if t.position < turn.position]
    if any(turn_is_private(t) for t in earlier):
        return True
    answered = [t for t in earlier if t.status == AskStatus.ANSWERED]
    if not answered:
        return False
    reach = reach_for(session)
    return any(_cites_private_now(session, reach, t) for t in answered)


def private_notices(turn: AskTurn) -> list[Problem]:
    log = RatchetLog.from_json(turn.escalation_json)
    if log.started_private:
        return [Problem(
            "This answer ran privately from the start.",
            "An earlier answer in this chat used private records, so everything after it stays private "
            "and goes only through the private, encrypted route.",
            "Nothing to do. Start a new chat for a question that doesn't need them.")]
    esc = log.first_private()
    if esc is None:
        return []
    return [Problem(
        "This answer turned private.",
        f"At step {esc.step} of the look-up it opened {esc.label}. Records like these are private. The steps "
        "before that carried none of them. From then on everything was sent only through the private, "
        "encrypted route and to no other AI service.",
        "Nothing to do. Private answers are shown only here in the Hub, never in Telegram or email, and can't "
        "be saved to the wiki.")]
