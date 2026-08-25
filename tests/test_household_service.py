from datetime import date, datetime, timedelta

from app.models.todo import Todo
from app.models.wiki import WikiPage
from app.services.household_service import get_household_data


def test_open_todos_and_recent_wiki(session):
    session.add(Todo(title="Pay EDP", due_date=date(2026, 9, 5), done=False))
    session.add(Todo(title="Pay water", due_date=date(2026, 8, 1), done=True))
    session.add(WikiPage(topic="Electricity", facts_json="{}", updated_at=datetime.utcnow()))
    session.add(WikiPage(
        topic="Old topic", facts_json="{}", updated_at=datetime.utcnow() - timedelta(days=30),
    ))
    session.commit()

    data = get_household_data(session, today=date(2026, 8, 17))

    assert len(data.open_todos) == 1
    assert data.open_todos[0].title == "Pay EDP"
    assert len(data.recently_changed_wiki_pages) == 1
    assert data.recently_changed_wiki_pages[0].topic == "Electricity"
