"""Routes for the household To-Do list and the per-domain backlog component."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel import Session, select

from app.db import get_session
from app.domains.registry import is_implemented
from app.models.domain import Domain
from app.models.todo import Todo
from app.services.todo_backlog import add_todo, backlog_context
from app.templating import templates

router = APIRouter(prefix="/todos", tags=["todos"])


def _todo_lists(session: Session):
    open_todos = session.exec(
        select(Todo).where(Todo.done == False).order_by(Todo.due_date)  # noqa: E712
    ).all()
    done_todos = session.exec(
        select(Todo).where(Todo.done == True).order_by(Todo.due_date.desc())  # noqa: E712
    ).all()
    return open_todos, done_todos


def _parse_domain(value: str) -> Domain:
    try:
        domain = Domain(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unknown domain") from None
    if not is_implemented(domain):
        raise HTTPException(status_code=400, detail="Unknown domain")
    return domain


@router.get("")
async def list_todos(request: Request, session: Session = Depends(get_session)):
    open_todos, done_todos = _todo_lists(session)
    return templates.TemplateResponse(
        request, "todos/list.html", {"open_todos": open_todos, "done_todos": done_todos}
    )


@router.post("/add")
async def add(
    request: Request,
    title: str = Form(...),
    domain: str = Form(...),
    due_date: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    target = _parse_domain(domain)
    try:
        due = date.fromisoformat(due_date) if due_date else None
        add_todo(session, title, target, due)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return templates.TemplateResponse(request, "todos/_backlog.html", backlog_context(session, target))


@router.post("/{todo_id}/done")
async def mark_done(
    request: Request, todo_id: int, backlog: Optional[str] = None, session: Session = Depends(get_session),
):
    todo = session.get(Todo, todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    todo.done = True
    session.add(todo)
    session.commit()
    if backlog:
        return templates.TemplateResponse(
            request, "todos/_backlog.html", backlog_context(session, _parse_domain(backlog))
        )
    open_todos, done_todos = _todo_lists(session)
    return templates.TemplateResponse(
        request, "todos/_lists.html", {"open_todos": open_todos, "done_todos": done_todos}
    )
