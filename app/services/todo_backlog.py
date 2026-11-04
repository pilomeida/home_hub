"""Per-domain to-do backlogs. Todos stay one shared table (the household
To-Dos page consolidates everything); a domain's backlog is simply its open
todos, filtered by Todo.domain. Every domain tab renders the same
todos/_backlog.html component from backlog_context()."""

from datetime import date
from typing import Optional

from sqlmodel import Session, func, select

from app.categories.registry import get_spec
from app.models.category import Category
from app.models.todo import Todo


def open_todos_for_domain(session: Session, domain: Category) -> list[Todo]:
    todos = session.exec(select(Todo).where(Todo.done == False, Todo.category == domain)).all()  # noqa: E712
    return sorted(todos, key=lambda t: (t.due_date is None, t.due_date or date.max, t.created_at))


def open_todo_count(session: Session, domain: Category) -> int:
    return session.exec(
        select(func.count()).select_from(Todo).where(Todo.done == False, Todo.category == domain)  # noqa: E712
    ).one()


def add_todo(session: Session, title: str, domain: Category, due_date: Optional[date] = None) -> Todo:
    clean = title.strip()
    if not clean:
        raise ValueError("A to-do needs a title")
    todo = Todo(title=clean, category=domain, due_date=due_date)
    session.add(todo)
    session.commit()
    session.refresh(todo)
    return todo


def backlog_context(session: Session, domain: Category) -> dict:
    return {
        "backlog_domain": domain,
        "backlog_label": get_spec(domain).label,
        "backlog_todos": open_todos_for_domain(session, domain),
    }
