from datetime import date, datetime, timedelta

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.todo import Todo
from app.models.transaction import Category, Transaction
from app.models.wiki import WikiPage
from app.services.dashboard_service import get_dashboard_data


def test_spend_totals_by_period(session):
    document = Document(
        filename="bill.pdf", file_path="/tmp/bill.pdf", content_hash="h1",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    session.add(Transaction(
        document_id=document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=50.0, currency="EUR", statement_period="2026-08",
    ))
    session.add(Transaction(
        document_id=document.id, provider="Vodafone", category=Category.TELECOM,
        amount=30.0, currency="EUR", statement_period="2026-08",
    ))
    session.add(Transaction(
        document_id=document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=45.0, currency="EUR", statement_period="2026-07",
    ))
    session.commit()

    data = get_dashboard_data(session, today=date(2026, 8, 17))

    assert data.spend_this_month == {"electricity": 50.0, "telecom": 30.0}
    assert data.spend_last_month == {"electricity": 45.0}


def test_open_todos_and_needs_attention_and_recent_wiki(session):
    session.add(Todo(title="Pay EDP", due_date=date(2026, 9, 5), done=False))
    session.add(Todo(title="Pay water", due_date=date(2026, 8, 1), done=True))
    session.add(Document(
        filename="bad.pdf", file_path="/tmp/bad.pdf", content_hash="h2",
        source=DocumentSource.MANUAL, status=DocumentStatus.NEEDS_ATTENTION,
    ))
    session.add(WikiPage(topic="Electricity", facts_json="{}", updated_at=datetime.utcnow()))
    session.add(WikiPage(
        topic="Old topic", facts_json="{}", updated_at=datetime.utcnow() - timedelta(days=30),
    ))
    session.commit()

    data = get_dashboard_data(session, today=date(2026, 8, 17))

    assert len(data.open_todos) == 1
    assert data.open_todos[0].title == "Pay EDP"
    assert len(data.needs_attention_documents) == 1
    assert len(data.recently_changed_wiki_pages) == 1
    assert data.recently_changed_wiki_pages[0].topic == "Electricity"
