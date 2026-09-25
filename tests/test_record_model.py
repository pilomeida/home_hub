import pytest
from sqlalchemy.exc import IntegrityError

from app.models.document import Document, DocumentSource
from app.models.domain import Domain
from app.models.record import Record
from app.models.todo import Todo


def test_record_defaults(session):
    record = Record(domain=Domain.HOUSE, category="maintenance_log", fields_json='{"item_name": "Boiler"}', entered_by="pedro@example.com")
    session.add(record)
    session.commit()
    session.refresh(record)
    assert record.id and record.document_id is None and record.retired_at is None and record.updated_at


def test_a_document_attaches_to_at_most_one_record(session):
    document = Document(filename="r.pdf", file_path="/tmp/r.pdf", content_hash="hr", source=DocumentSource.MANUAL)
    session.add(document)
    session.commit()
    session.refresh(document)
    session.add(Record(domain=Domain.HOUSE, category="maintenance_log", document_id=document.id))
    session.commit()
    session.add(Record(domain=Domain.HOUSE, category="maintenance_log", document_id=document.id))
    with pytest.raises(IntegrityError):
        session.commit()


def test_todo_can_link_to_a_record(session):
    record = Record(domain=Domain.HOUSE, category="maintenance_log")
    session.add(record)
    session.commit()
    session.refresh(record)
    todo = Todo(title="Service the boiler", record_id=record.id)
    session.add(todo)
    session.commit()
    session.refresh(todo)
    assert todo.record_id == record.id
