"""Ask: cross-domain natural-language chat (wiki-first)."""

import json

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session

from app.db import get_session, get_session_factory
from app.llm_gateway import GatewayClient, get_gateway
from app.models.ask import AskConversation, AskStatus, AskTurn
from app.services.ask import actions as ask_actions
from app.services.ask import messages as ask_messages
from app.services.ask.citations import render_turn
from app.services.ask.conversation import TurnInProgress, add_turn, recent_conversations, start_conversation, turns_of
from app.services.ask.engine import run_turn
from app.services.ask.private import turn_is_private
from app.services.ask.save import AnswerNotSaveable, save_answer_as_wiki_page
from app.templating import templates
from app.ui import ask_visual

router = APIRouter(prefix="/ask", tags=["ask"])

_MAX_QUESTION_CHARS = 1000


def get_ask_gateway() -> GatewayClient:
    """The gateway client for Ask. Tests override this dependency."""
    return get_gateway()


def _clean(question: str) -> str:
    question = question.strip()
    if not question or len(question) > _MAX_QUESTION_CHARS:
        raise HTTPException(status_code=400, detail=f"Question must be 1–{_MAX_QUESTION_CHARS} characters")
    return question


def _about(request: Request, session: Session, question: str) -> str:
    """The Who chip (step 10i): with one person chosen, the question says who it is about, in words the person
    sees on the page (nothing hidden is added to what the AI reads)."""
    from app.services import who
    person = who.chosen(session, request.cookies.get(who.COOKIE))
    if person is None or question.lower().startswith(f"about {person.name.lower()}:"):
        return question
    return f"About {person.name}: {question}"


def _action_view(session: Session, action) -> dict:
    return {"action": action, "headers": ask_actions.card_headers(action),
            "rows": ask_actions.card_rows(session, action) if action.status == ask_actions.PROPOSED else [],
            "outcome": ask_actions.result(action) if action.tier == ask_actions.CONFIRM or action.kind == ask_actions.ADD_PAPER
            else {}}


def _turn_view(turn: AskTurn, session: Optional[Session] = None) -> dict:
    failed = ask_messages.failure(turn) if turn.status == AskStatus.FAILED else None
    actions = [_action_view(session, a) for a in ask_actions.for_turn(session, turn.id)] if session is not None else []
    answered = turn.status == AskStatus.ANSWERED
    citations = json.loads(turn.citations_json or "[]") if answered else []
    return {"turn": turn, "actions": actions,
            "visual": ask_visual.render(turn.visual_json, citations) if answered else "",
            "open_url": citations[0]["url"] if citations else None, "rendered": render_turn(turn) if turn.status == AskStatus.ANSWERED else None,
            "failure": failed[0] if failed else None, "failure_technical": failed[1] if failed else None,
            "notices": [] if turn.status == AskStatus.PENDING else ask_messages.notices(turn),
            "private": turn_is_private(turn)}


def get_ask_inbox_classifier():
    """What reads an attached paper no rule recognises (as the Inbox's "Add a paper"). Tests override this."""
    from app.services import inbox_service
    return inbox_service.suggest_category_and_kind


async def _attached(request: Request, session: Session, file: Optional[UploadFile], classify):
    """A file attached to a question goes to the Inbox as a suggestion (step 10g), checked before anything else."""
    from app.services import inbox_service
    if file is None or not file.filename:
        return None
    content = await file.read(inbox_service.MAX_PAGE_UPLOAD_BYTES + 1)
    try:
        return await inbox_service.add_from_page(session, file.filename, content, note=None, classify=classify,
                                                 uploaded_by=getattr(request.state, "user_email", None))
    except inbox_service.UploadRefused as exc:
        raise HTTPException(status_code=400, detail=exc.problem.as_detail())


def _paper_only(session: Session, turn: AskTurn) -> None:
    """A file with no question: nothing to ask the AI, so the turn is answered by the paper's card alone (no text,
    so it is never replayed to the AI as history: the paper is not filed yet)."""
    from datetime import datetime
    turn.status, turn.answered_at = AskStatus.ANSWERED, datetime.utcnow()
    session.add(turn)
    session.commit()


async def _answer_in_background(session_factory, turn_id: int, gateway) -> None:
    with session_factory() as session:
        await run_turn(session, turn_id, gateway=gateway)


@router.get("")
async def ask_home(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "ask/home.html", {"conversations": recent_conversations(session)})


@router.post("")
async def new_chat(request: Request, background_tasks: BackgroundTasks, question: str = Form(""),
                   file: Optional[UploadFile] = File(None),
                   session: Session = Depends(get_session), session_factory=Depends(get_session_factory),
                   gateway=Depends(get_ask_gateway), classify=Depends(get_ask_inbox_classifier)):
    has_file = file is not None and bool(file.filename)
    asked = _about(request, session, _clean(question)) if question.strip() or not has_file else None
    receipt = await _attached(request, session, file, classify)
    started_by = getattr(request.state, "user_email", None)
    turn = start_conversation(session, asked or "Added a paper", started_by=started_by)
    if receipt is not None:
        ask_actions.paper_attached(session, turn.id, receipt)
    if asked:
        background_tasks.add_task(_answer_in_background, session_factory, turn.id, gateway)
    else:
        _paper_only(session, turn)
    url = f"/ask/c/{turn.conversation_id}"
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


@router.get("/c/{conversation_id}")
async def conversation_page(request: Request, conversation_id: int, session: Session = Depends(get_session)):
    conv = session.get(AskConversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    turns = [_turn_view(t, session) for t in turns_of(session, conversation_id)]
    return templates.TemplateResponse(request, "ask/conversation.html", {"conversation": conv, "turns": turns})


@router.post("/c/{conversation_id}/turns")
async def follow_up(request: Request, conversation_id: int, background_tasks: BackgroundTasks,
                    question: str = Form(""), file: Optional[UploadFile] = File(None),
                    session: Session = Depends(get_session), session_factory=Depends(get_session_factory),
                    gateway=Depends(get_ask_gateway), classify=Depends(get_ask_inbox_classifier)):
    has_file = file is not None and bool(file.filename)
    asked = _about(request, session, _clean(question)) if question.strip() or not has_file else None
    try:
        turn = add_turn(session, conversation_id, asked or "Added a paper",
                        asked_by=getattr(request.state, "user_email", None))
    except KeyError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    except TurnInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    try:
        receipt = await _attached(request, session, file, classify)
    except HTTPException:
        session.delete(turn)            # the file was refused: nothing is left behind
        session.commit()
        raise
    if receipt is not None:
        ask_actions.paper_attached(session, turn.id, receipt)
    if asked:
        background_tasks.add_task(_answer_in_background, session_factory, turn.id, gateway)
    else:
        _paper_only(session, turn)
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "ask/_turn.html", _turn_view(turn, session))
    return RedirectResponse(f"/ask/c/{conversation_id}", status_code=303)


@router.get("/turns/{turn_id}")
async def turn_partial(request: Request, turn_id: int, session: Session = Depends(get_session)):
    turn = session.get(AskTurn, turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return templates.TemplateResponse(request, "ask/_turn.html", _turn_view(turn, session))


@router.post("/turns/{turn_id}/save")
async def save_turn(turn_id: int, title: str = Form(""), session: Session = Depends(get_session)):
    try:
        page = save_answer_as_wiki_page(session, turn_id, title)
    except AnswerNotSaveable as exc:
        p = exc.problem
        raise HTTPException(status_code=400, detail=(
            {"what": p.what, "why": p.why, "next_step": p.next_step} if p else str(exc)))
    return RedirectResponse(f"/wiki/{page.id}", status_code=303)


# --- what an answer did or proposed (PLAN step 10g) ---------------------------------------------------------------

def _action_or_404(session: Session, action_id: int):
    from app.models.ask import AskAction
    action = session.get(AskAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail={
            "what": "Nothing changed.", "why": "That action is not in the Hub any more.",
            "next_step": "Reload the conversation and ask again if it is still needed."})
    return action


async def _decide(request: Request, session: Session, action_id: int, how) -> Response:
    action = _action_or_404(session, action_id)
    try:
        result = how(session, action, by=getattr(request.state, "user_email", None))
        if hasattr(result, "__await__"):
            result = await result
    except ask_actions.ActionRefused as exc:
        raise HTTPException(status_code=409, detail=exc.problem.as_detail())
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "ask/_action.html", {"view": _action_view(session, result)})
    turn = session.get(AskTurn, result.turn_id)
    return RedirectResponse(f"/ask/c/{turn.conversation_id}#turn-{turn.id}", status_code=303)


@router.post("/actions/{action_id}/undo")
async def undo_action(request: Request, action_id: int, session: Session = Depends(get_session)):
    return await _decide(request, session, action_id, ask_actions.undo)


@router.post("/actions/{action_id}/confirm")
async def confirm_action(request: Request, action_id: int, session: Session = Depends(get_session)):
    return await _decide(request, session, action_id, ask_actions.confirm)


@router.post("/actions/{action_id}/cancel")
async def cancel_action(request: Request, action_id: int, session: Session = Depends(get_session)):
    return await _decide(request, session, action_id, ask_actions.cancel)
