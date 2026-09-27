"""Ask: cross-domain natural-language chat (wiki-first)."""

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session

from app.db import get_session, get_session_factory
from app.llm_gateway import GatewayClient, get_gateway
from app.models.ask import AskConversation, AskStatus, AskTurn
from app.services.ask.citations import render_turn
from app.services.ask.conversation import TurnInProgress, add_turn, recent_conversations, start_conversation, turns_of
from app.services.ask.engine import run_turn
from app.services.ask.save import AnswerNotSaveable, save_answer_as_wiki_page
from app.templating import templates

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


def _turn_view(turn: AskTurn) -> dict:
    return {"turn": turn, "rendered": render_turn(turn) if turn.status == AskStatus.ANSWERED else None}


async def _answer_in_background(session_factory, turn_id: int, gateway) -> None:
    with session_factory() as session:
        await run_turn(session, turn_id, gateway=gateway)


@router.get("")
async def ask_home(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "ask/home.html", {"conversations": recent_conversations(session)})


@router.post("")
async def new_chat(request: Request, background_tasks: BackgroundTasks, question: str = Form(""),
                   session: Session = Depends(get_session), session_factory=Depends(get_session_factory),
                   gateway=Depends(get_ask_gateway)):
    turn = start_conversation(session, _clean(question), started_by=getattr(request.state, "user_email", None))
    background_tasks.add_task(_answer_in_background, session_factory, turn.id, gateway)
    url = f"/ask/c/{turn.conversation_id}"
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


@router.get("/c/{conversation_id}")
async def conversation_page(request: Request, conversation_id: int, session: Session = Depends(get_session)):
    conv = session.get(AskConversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    turns = [_turn_view(t) for t in turns_of(session, conversation_id)]
    return templates.TemplateResponse(request, "ask/conversation.html", {"conversation": conv, "turns": turns})


@router.post("/c/{conversation_id}/turns")
async def follow_up(request: Request, conversation_id: int, background_tasks: BackgroundTasks,
                    question: str = Form(""), session: Session = Depends(get_session),
                    session_factory=Depends(get_session_factory), gateway=Depends(get_ask_gateway)):
    try:
        turn = add_turn(session, conversation_id, _clean(question),
                        asked_by=getattr(request.state, "user_email", None))
    except KeyError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    except TurnInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    background_tasks.add_task(_answer_in_background, session_factory, turn.id, gateway)
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "ask/_turn.html", _turn_view(turn))
    return RedirectResponse(f"/ask/c/{conversation_id}", status_code=303)


@router.get("/turns/{turn_id}")
async def turn_partial(request: Request, turn_id: int, session: Session = Depends(get_session)):
    turn = session.get(AskTurn, turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return templates.TemplateResponse(request, "ask/_turn.html", _turn_view(turn))


@router.post("/turns/{turn_id}/save")
async def save_turn(turn_id: int, title: str = Form(""), session: Session = Depends(get_session)):
    try:
        page = save_answer_as_wiki_page(session, turn_id, title)
    except AnswerNotSaveable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse(f"/wiki/{page.id}", status_code=303)
