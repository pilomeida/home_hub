"""Routes for the To-Do list."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from app.db import get_session
from app.models.todo import Todo
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

@router.get("")
async def list_todos(request: Request, session: Session = Depends(get_session)):
    open_todos, done_todos = _todo_lists(session)
    return templates.TemplateResponse(
        request, "todos/list.html", {"open_todos": open_todos, "done_todos": done_todos}
    )

@router.post("/{todo_id}/done")
async def mark_done(request: Request, todo_id: int, session: Session = Depends(get_session)):
    todo = session.get(Todo, todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    todo.done = True
    session.add(todo)
    session.commit()
    open_todos, done_todos = _todo_lists(session)
    return templates.TemplateResponse(
        request, "todos/_lists.html", {"open_todos": open_todos, "done_todos": done_todos}
    )
