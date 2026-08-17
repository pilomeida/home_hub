from datetime import date

from app.models.todo import Todo


def test_list_todos_renders(client, session):
    session.add(Todo(title="Pay EDP", due_date=date(2026, 9, 5)))
    session.commit()

    response = client.get("/todos")

    assert response.status_code == 200
    assert "Pay EDP" in response.text


def test_mark_done_updates_status(client, session):
    todo = Todo(title="Pay water", due_date=date(2026, 9, 1))
    session.add(todo)
    session.commit()
    session.refresh(todo)

    response = client.post(f"/todos/{todo.id}/done")

    assert response.status_code == 200
    session.refresh(todo)
    assert todo.done is True


def test_mark_done_404_for_missing_todo(client):
    response = client.post("/todos/9999/done")
    assert response.status_code == 404
