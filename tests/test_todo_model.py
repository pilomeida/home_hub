from datetime import date

from app.models.todo import Todo


def test_create_and_read_todo(session):
    todo = Todo(title="Pay EDP", due_date=date(2026, 9, 5))
    session.add(todo)
    session.commit()
    session.refresh(todo)

    assert todo.id is not None
    assert todo.done is False
