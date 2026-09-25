"""Canonical ref strings and Citables for the shared record types.
Sources are a Document ("doc:N") or a hand-entered Record ("rec:N")."""

from typing import Union

from app.domains import registry
from app.domains.fields import describe_document, file_url
from app.domains.registry import get_spec, is_implemented
from app.models.document import Document
from app.models.record import Record
from app.models.todo import Todo
from app.services.ask.contracts import Citable

Source = Union[Document, Record]


def source_ref(source: Source) -> str:
    return f"rec:{source.id}" if isinstance(source, Record) else f"doc:{source.id}"


def citable_for_wiki_page(page_id: int, title: str) -> Citable:
    return Citable(ref=f"wiki:{page_id}", label=f"Wiki: {title}", url=f"/wiki/{page_id}")


def citable_for_document(doc: Document) -> Citable:
    desc = describe_document(doc)
    return Citable(ref=f"doc:{doc.id}", label=f"{desc.domain_label or 'Household'} document: {doc.filename}",
                   url=desc.url or file_url(doc))


def citable_for_record(record: Record) -> Citable:
    spec = get_spec(record.domain)
    label = f"{spec.label} record: {spec.category_label(record.category)} (entered {record.created_at:%d %b %Y})"
    return Citable(ref=f"rec:{record.id}", label=label, url=registry.record_url(record) or spec.home_url)


def citable_for_source(source: Source) -> Citable:
    return citable_for_record(source) if isinstance(source, Record) else citable_for_document(source)


def citable_for_todo(todo: Todo) -> Citable:
    base = get_spec(todo.domain).home_url if is_implemented(todo.domain) else "/todos"
    return Citable(ref=f"todo:{todo.id}", label=f"To-do: {todo.title}", url=f"{base}#todo-{todo.id}")
