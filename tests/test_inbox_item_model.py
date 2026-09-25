from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.inbox_item import InboxItem


def _document(session, content_hash="h-inbox"):
    document = Document(
        filename="scan.jpg", file_path="/tmp/scan.jpg", content_hash=content_hash,
        source=DocumentSource.TELEGRAM, status=DocumentStatus.PENDING_REVIEW,
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def test_new_statuses_and_source_exist():
    assert DocumentStatus.PENDING_REVIEW.value == "pending_review"
    assert DocumentStatus.DISCARDED.value == "discarded"
    assert DocumentSource.TELEGRAM.value == "telegram"


def test_inbox_item_round_trip(session):
    document = _document(session)
    item = InboxItem(
        document_id=document.id, context_text="Telegram caption: boiler warranty",
        external_ref="telegram:1:2", suggested_domain="some_future_domain",
        suggested_category="anything", confidence=0.91, classifier_note="looks like it",
    )
    session.add(item)
    session.commit()
    session.refresh(item)

    assert item.suggested_domain == "some_future_domain"   # plain string, no DB enum
    assert isinstance(item.received_at, datetime)
    assert item.reviewed_by is None and item.reviewed_at is None


def test_inbox_item_one_per_document(session):
    document = _document(session, content_hash="h-unique")
    session.add(InboxItem(document_id=document.id))
    session.commit()
    session.add(InboxItem(document_id=document.id))
    with pytest.raises(IntegrityError):
        session.commit()
