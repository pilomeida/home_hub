from datetime import date

import pytest

from app.models.domain import Domain
from app.models.todo import Todo
from app.services.todo_backlog import add_todo, backlog_context, open_todo_count, open_todos_for_domain


def test_open_todos_for_domain_filters_and_orders(session):
    session.add(Todo(title="undated house", domain=Domain.HOUSE))
    session.add(Todo(title="later house", domain=Domain.HOUSE, due_date=date(2026, 12, 1)))
    session.add(Todo(title="sooner house", domain=Domain.HOUSE, due_date=date(2026, 10, 1)))
    session.add(Todo(title="done house", domain=Domain.HOUSE, done=True))
    session.add(Todo(title="money", domain=Domain.FINANCIALS))
    session.commit()

    titles = [t.title for t in open_todos_for_domain(session, Domain.HOUSE)]

    assert titles == ["sooner house", "later house", "undated house"]
    assert open_todo_count(session, Domain.HOUSE) == 3
    assert open_todo_count(session, Domain.FINANCIALS) == 1


def test_add_todo_tags_domain_and_rejects_blank(session):
    todo = add_todo(session, "  Clean gutters ", Domain.HOUSE, date(2026, 11, 1))
    assert todo.title == "Clean gutters" and todo.domain == Domain.HOUSE and todo.due_date == date(2026, 11, 1)
    with pytest.raises(ValueError):
        add_todo(session, "   ", Domain.HOUSE)


def test_backlog_context_uses_registry_label(session):
    context = backlog_context(session, Domain.FINANCIALS)
    assert context["backlog_label"] == "Financials"
    assert context["backlog_domain"] == Domain.FINANCIALS
    assert context["backlog_todos"] == []
