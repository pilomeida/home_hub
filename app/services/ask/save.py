"""Files one useful Ask answer back into the wiki (LLM-Wiki 'Query' op),
only through Plan A's apply_claims / add_link."""

from __future__ import annotations

from sqlmodel import Session

from app.models.ask import AskStatus, AskTurn
from app.models.document import Document
from app.models.record import Record
from app.models.wiki import ANSWER_PAGE_TYPE, WikiOperation, WikiPage
from app.services.ask.citations import cited_refs, known_from_turn, plain_text
from app.services.ask.conversation import turns_of
from app.services.wiki_store import ClaimInput, PageRef, add_link, apply_claims


class AnswerNotSaveable(Exception):
    pass


def _ids(refs: list[str], prefix: str) -> list[int]:
    return [int(r.split(":", 1)[1]) for r in refs if r.startswith(f"{prefix}:")]


def _standalone_question(session: Session, turn: AskTurn) -> str:
    earlier = [t for t in turns_of(session, turn.conversation_id) if t.position < turn.position]
    return f"{earlier[-1].question} → {turn.question}" if earlier else turn.question


def save_answer_as_wiki_page(session: Session, turn_id: int, title: str) -> WikiPage:
    turn = session.get(AskTurn, turn_id)
    if turn is None or turn.status != AskStatus.ANSWERED or not turn.answer_text:
        raise AnswerNotSaveable("Only answered questions can be saved")
    if turn.saved_wiki_page_id:
        return session.get(WikiPage, turn.saved_wiki_page_id)

    known = known_from_turn(turn)
    refs = cited_refs(turn.answer_text, known)
    wiki_ids, doc_ids, rec_ids = _ids(refs, "wiki"), _ids(refs, "doc"), _ids(refs, "rec")
    domains = {p.domain for p in (session.get(WikiPage, i) for i in wiki_ids) if p} | \
              {d.domain for d in (session.get(Document, i) for i in doc_ids) if d} | \
              {r.domain for r in (session.get(Record, i) for i in rec_ids) if r}
    domain = next(iter(domains)) if len(domains) == 1 else None

    title = (title or turn.question).strip()[:200]
    question = _standalone_question(session, turn)
    ref = PageRef(page_type=ANSWER_PAGE_TYPE, title=title, summary=f"Saved answer to: {question}"[:200])
    report = apply_claims(session, [
        ClaimInput(page=ref, key="question", value=question, label="Question"),
        ClaimInput(page=ref, key="answer", value=plain_text(turn.answer_text, known), label="Answer"),
        ClaimInput(page=ref, key="sources", value="; ".join(f"{r} {known[r].label}" for r in refs) or "none",
                   label="Sources"),
    ], document=None, domain=domain, operation=WikiOperation.QUERY, description=f"Saved Ask answer as “{title}”")
    page_id = report.page_ids[0]
    for wiki_id in wiki_ids:
        if wiki_id != page_id:
            add_link(session, page_id, wiki_id)

    turn.saved_wiki_page_id = page_id
    session.add(turn)
    session.commit()
    return session.get(WikiPage, page_id)
