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


from app.models.domain import Domain


def test_add_todo_returns_the_domain_backlog(client, session):
    response = client.post("/todos/add", data={"title": "Check smoke alarms", "domain": "financials", "due_date": "2026-10-10"})

    assert response.status_code == 200
    assert 'id="backlog-financials"' in response.text
    assert "Check smoke alarms" in response.text


def test_add_todo_rejects_unknown_domain_and_bad_date(client):
    assert client.post("/todos/add", data={"title": "x", "domain": "nope"}).status_code == 400
    assert client.post("/todos/add", data={"title": "x", "domain": "financials", "due_date": "10/10"}).status_code == 400
    assert client.post("/todos/add", data={"title": " ", "domain": "financials"}).status_code == 400


def test_mark_done_from_a_backlog_returns_the_backlog(client, session):
    todo = Todo(title="Pay IMI", domain=Domain.FINANCIALS)
    session.add(todo)
    session.commit()
    session.refresh(todo)

    response = client.post(f"/todos/{todo.id}/done?backlog=financials")

    assert response.status_code == 200
    assert 'id="backlog-financials"' in response.text
    assert "Pay IMI" not in response.text


def test_todos_page_shows_domain_badges(client, session):
    session.add(Todo(title="Pay EDP", domain=Domain.FINANCIALS))
    session.commit()
    assert '<span class="badge">Financials</span>' in client.get("/todos").text
