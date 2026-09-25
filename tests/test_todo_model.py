from datetime import date

from app.models.todo import Todo


def test_create_and_read_todo(session):
    todo = Todo(title="Pay EDP", due_date=date(2026, 9, 5))
    session.add(todo)
    session.commit()
    session.refresh(todo)

    assert todo.id is not None
    assert todo.done is False


def test_todo_domain_defaults_to_none(session):
    todo = Todo(title="Pay EDP", due_date=date(2026, 9, 5))
    session.add(todo)
    session.commit()
    session.refresh(todo)

    assert todo.domain is None


def test_todo_domain_can_be_set(session):
    from app.models.domain import Domain

    todo = Todo(title="Pay EDP", due_date=date(2026, 9, 5), domain=Domain.FINANCIALS)
    session.add(todo)
    session.commit()
    session.refresh(todo)

    assert todo.domain == Domain.FINANCIALS


def test_todo_can_link_to_a_document(session):
    from app.models.document import Document, DocumentSource

    document = Document(
        filename="w.pdf", file_path="/tmp/w.pdf", content_hash="hash-todo-link",
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    unlinked = Todo(title="Something")
    linked = Todo(title="Renew Boiler warranty", document_id=document.id)
    session.add(unlinked)
    session.add(linked)
    session.commit()
    session.refresh(unlinked)
    session.refresh(linked)

    assert unlinked.document_id is None
    assert linked.document_id == document.id
