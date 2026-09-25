"""Direct-to-table builders for knowledge-layer and Ask tests."""

import itertools
import json
from datetime import datetime
from typing import Optional

from sqlmodel import select

from app.models.ask import AskConversation, AskStatus, AskTurn
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.record import Record
from app.models.wiki import (
    TOPIC_PAGE_TYPE, ClaimStatus, WikiClaim, WikiClaimSource, WikiLink, WikiLogEntry, WikiOperation, WikiPage,
)

_seq = itertools.count(1)


def make_document(session, *, domain: Optional[Domain] = Domain.HOUSE, status=DocumentStatus.PROCESSED,
                  category: Optional[str] = "manual", fields: Optional[dict] = None, filename="doc.pdf",
                  file_path="/tmp/doc.pdf", created_at: Optional[datetime] = None) -> Document:
    doc = Document(filename=filename, file_path=file_path, content_hash=f"h-{next(_seq)}",
                   source=DocumentSource.MANUAL, status=status, domain=domain, category=category,
                   fields_json=json.dumps(fields or {}))
    if created_at:
        doc.created_at = created_at
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def make_page(session, topic, *, domain: Optional[Domain] = Domain.HOUSE, page_type=TOPIC_PAGE_TYPE,
              summary: Optional[str] = None, entity_key: Optional[str] = None) -> WikiPage:
    page = WikiPage(topic=topic, domain=domain, page_type=page_type, summary=summary, entity_key=entity_key)
    session.add(page)
    session.commit()
    session.refresh(page)
    return page


def make_record(session, *, domain=Domain.HOUSE, category="visit", fields: Optional[dict] = None,
                document_id: Optional[int] = None, retired: bool = False,
                created_at: Optional[datetime] = None) -> Record:
    record = Record(domain=domain, category=category, fields_json=json.dumps(fields or {}),
                    document_id=document_id, retired_at=datetime(2026, 1, 1) if retired else None)
    if created_at:
        record.created_at = created_at
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def make_claim(session, page, key, value, *, doc_ids=(), record_ids=(), withdrawn_doc_ids=(),
               note: Optional[str] = None, created_at: Optional[datetime] = None,
               superseded_at: Optional[datetime] = None) -> WikiClaim:
    claim = WikiClaim(page_id=page.id, key=key, value=value, note=note,
                      status=ClaimStatus.SUPERSEDED if superseded_at else ClaimStatus.ACTIVE,
                      superseded_at=superseded_at)
    if created_at:
        claim.created_at = created_at
    session.add(claim)
    session.commit()
    session.refresh(claim)
    for doc_id in doc_ids:
        session.add(WikiClaimSource(claim_id=claim.id, document_id=doc_id))
    for record_id in record_ids:
        session.add(WikiClaimSource(claim_id=claim.id, record_id=record_id))
    for doc_id in withdrawn_doc_ids:
        session.add(WikiClaimSource(claim_id=claim.id, document_id=doc_id, withdrawn_at=datetime(2026, 2, 1)))
    session.commit()
    return claim


def make_link(session, from_page, to_page) -> None:
    session.add(WikiLink(from_page_id=from_page.id, to_page_id=to_page.id))
    session.commit()


def make_ingest_log(session, source) -> None:
    is_record = isinstance(source, Record)
    session.add(WikiLogEntry(operation=WikiOperation.INGEST, description="ingested",
                             document_id=None if is_record else source.id,
                             record_id=source.id if is_record else None))
    session.commit()


def make_conversation(session, title="Chat", started_by=None) -> AskConversation:
    conv = AskConversation(title=title, started_by=started_by)
    session.add(conv)
    session.commit()
    session.refresh(conv)
    return conv


def make_turn(session, conv, question, *, status=AskStatus.ANSWERED, answer_text=None,
              citations: Optional[list[dict]] = None, position: Optional[int] = None) -> AskTurn:
    if position is None:
        position = len(session.exec(select(AskTurn).where(AskTurn.conversation_id == conv.id)).all()) + 1
    turn = AskTurn(conversation_id=conv.id, position=position, question=question, status=status,
                   answer_text=answer_text, citations_json=json.dumps(citations or []))
    session.add(turn)
    session.commit()
    session.refresh(turn)
    return turn
