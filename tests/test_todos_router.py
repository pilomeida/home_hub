from datetime import date

from sqlmodel import select

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


from app.models.category import Category


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
    todo = Todo(title="Pay IMI", category=Category.FINANCIALS)
    session.add(todo)
    session.commit()
    session.refresh(todo)

    response = client.post(f"/todos/{todo.id}/done?backlog=financials")

    assert response.status_code == 200
    assert 'id="backlog-financials"' in response.text
    assert "Pay IMI" not in response.text


def test_todos_page_shows_domain_badges(client, session):
    session.add(Todo(title="Pay EDP", category=Category.FINANCIALS))
    session.commit()
    assert '<span class="badge">Financials</span>' in client.get("/todos").text


def test_add_general_todo_has_no_area_and_shows_on_the_todos_page_and_dashboard_not_in_an_area(client, session):
    response = client.post("/todos/add-general", data={"title": "Re-register the Ergon domain", "due_date": "2026-12-16"})

    assert response.status_code == 200
    assert "Re-register the Ergon domain" in response.text and "due 2026-12-16" in response.text
    todo = session.exec(select(Todo).where(Todo.title == "Re-register the Ergon domain")).one()
    assert todo.category is None and todo.due_date == date(2026, 12, 16)
    assert '<span class="badge">' not in response.text
    assert "Re-register the Ergon domain" in client.get("/todos").text


def test_add_general_todo_says_what_why_and_next_step_when_it_cannot_add(client):
    for data in ({"title": " "}, {"title": "x", "due_date": "16/12"}):
        response = client.post("/todos/add-general", data=data)
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["what"] == "To-do not added." and detail["why"] and detail["next_step"]


def test_a_todo_without_a_due_date_does_not_say_due_none(client, session):
    session.add(Todo(title="Someday thing"))
    session.commit()
    text = client.get("/todos").text
    assert "Someday thing" in text and "due None" not in text


def test_the_todos_page_offers_the_general_form(client):
    assert 'hx-post="/todos/add-general"' in client.get("/todos").text


def test_the_dashboard_does_not_say_due_none_for_a_to_do_without_a_date(client, session):
    session.add(Todo(title="Someday thing on the dashboard"))
    session.commit()
    text = client.get("/overview").text
    assert "Someday thing on the dashboard" in text and "due None" not in text


def test_add_general_todo_works_without_a_due_date(client, session):
    response = client.post("/todos/add-general", data={"title": "No date needed"})
    assert response.status_code == 200 and "No date needed" in response.text and "due None" not in response.text
    assert session.exec(select(Todo).where(Todo.title == "No date needed")).one().due_date is None
