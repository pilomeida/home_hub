from datetime import date

from app.models.todo import Todo


def test_dashboard_renders_open_todos(client, session):
    session.add(Todo(title="Pay EDP", due_date=date(2026, 9, 5)))
    session.commit()

    response = client.get("/")

    assert response.status_code == 200
    assert "Pay EDP" in response.text


def test_cash_flow_chart_partial_route_responds(client, session):
    response = client.get("/cash-flow-chart", params={"range": "6m"})

    assert response.status_code == 200
    # The partial re-render must not include the full page chrome.
    assert "<html" not in response.text
