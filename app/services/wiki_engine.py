"""The wiki's Ingest operation: WHAT a source document contributes to the
knowledge layer. Storage rules (supersede-not-delete, source links, log)
live in app.services.wiki_store.

Two sources of claims, both driven by the domain's registry WikiSchema:
- claims_from_fields: deterministic, from the document's validated fields
  (entity pages such as a House item) -- no LLM;
- assess_document_for_wiki: an LLM pass for free-form topic pages, only for
  domains whose schema gives `guidance` (Financials today).
ingest_into_wiki is the only entry point; domain handlers call it after a
document is finalized, so documents awaiting review never touch the wiki."""

from __future__ import annotations

import json
from typing import Optional

from anthropic import AsyncAnthropic
from sqlmodel import Session, select

from app.config import settings
from app.domains.base import DomainSpec
from app.domains.fields import effective_fields, load_fields
from app.domains.registry import get_spec
from app.models.document import Document
from app.models.record import Record
from app.models.wiki import TOPIC_PAGE_TYPE, WikiOperation, WikiPage
from app.services.json_utils import strip_json_fences
from app.services.wiki_store import ClaimInput, IngestReport, LinkInput, PageRef, apply_claims, normalize_entity_key

_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_TEMPLATE = """You maintain the "{label}" section of a family's household wiki.
{guidance}

A wiki page records standing facts worth remembering later (a provider, a \
contract or policy number, a tariff, a renewal date) -- never one-off \
transactional data such as this month's amount due.

Existing pages in this section (reuse a title exactly when the facts belong there):
{existing}

Respond with ONLY a JSON object:
{{"pages": [{{"title": "short human title", "summary": "one line describing the page", "facts": {{"key": "value"}}}}]}}
Return {{"pages": []}} when the document holds nothing worth recording."""


class WikiAssessmentError(Exception):
    pass


def _fact_fields(spec: DomainSpec, source: Document | Record) -> dict[str, str]:
    return {**effective_fields(spec, source.category, load_fields(source)),
            "category": spec.category_label(source.category)}


def claims_from_fields(spec: DomainSpec, document: Document | Record) -> list[ClaimInput]:
    fields = _fact_fields(spec, document)
    claims: list[ClaimInput] = []
    for entity_type in spec.wiki.entity_types:
        if document.category not in entity_type.categories:
            continue
        name = fields.get(entity_type.key_field)
        if not name:
            continue
        page = PageRef(page_type=entity_type.page_type, title=name, entity_key=normalize_entity_key(name))
        for fact in entity_type.facts:
            if fact.categories is not None and document.category not in fact.categories:
                continue
            value = fields.get(fact.field)
            if value:
                claims.append(ClaimInput(page=page, key=fact.key, value=value, label=fact.label, policy=fact.policy,
                                         note=fields.get(fact.note_field) if fact.note_field else None))
    return claims


def links_from_fields(spec: DomainSpec, document: Document | Record) -> list[LinkInput]:
    fields = _fact_fields(spec, document)
    links: list[LinkInput] = []
    for entity_type in spec.wiki.entity_types:
        if document.category not in entity_type.categories:
            continue
        name = fields.get(entity_type.key_field)
        if not name:
            continue
        source = PageRef(page_type=entity_type.page_type, title=name, entity_key=normalize_entity_key(name))
        for link in entity_type.links:
            if link.categories is not None and document.category not in link.categories:
                continue
            target_name = fields.get(link.field)
            if target_name:
                target = PageRef(page_type=link.target_page_type, title=target_name,
                                 entity_key=normalize_entity_key(target_name))
                links.append(LinkInput(from_page=source, to_page=target, bidirectional=link.bidirectional))
    return links


async def assess_document_for_wiki(
    session: Session,
    document: Document,
    context: str,
    client: Optional[AsyncAnthropic] = None,
) -> list[ClaimInput]:
    spec = get_spec(document.domain)
    if not spec.wiki.guidance:
        return []
    existing = session.exec(
        select(WikiPage.topic).where(WikiPage.domain == document.domain, WikiPage.page_type == TOPIC_PAGE_TYPE)
        .order_by(WikiPage.topic)
    ).all()
    system = _SYSTEM_TEMPLATE.format(
        label=spec.label, guidance=spec.wiki.guidance,
        existing="\n".join(f"- {title}" for title in existing) or "(none yet)",
    )
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = await anthropic_client.messages.create(
        model=_MODEL,
        max_tokens=1024,
        thinking={"type": "disabled"},
        system=system,
        messages=[{"role": "user", "content": context}],
    )
    try:
        data = json.loads(strip_json_fences(message.content[0].text))
        claims = []
        for page in data.get("pages", []):
            ref = PageRef(page_type=TOPIC_PAGE_TYPE, title=str(page["title"]).strip(), summary=page.get("summary"))
            for key, value in (page.get("facts") or {}).items():
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                claims.append(ClaimInput(page=ref, key=str(key), value=text, label=str(key)))
        return claims
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise WikiAssessmentError(f"Could not parse wiki assessment: {exc}") from exc


async def ingest_into_wiki(
    session: Session,
    document: Document | Record,
    *,
    context: Optional[str] = None,
    operation: WikiOperation = WikiOperation.INGEST,
    client: Optional[AsyncAnthropic] = None,
) -> IngestReport:
    """Always writes exactly one wiki_log entry (`operation`, INGEST by default), even when nothing
    changes; Plan C's lint relies on it to find never-ingested documents. Accepts either a Document
    or a Record (a hand-entered source); a re-ingest always reflects the source's current fields."""
    spec = get_spec(document.domain)
    is_record = isinstance(document, Record)
    claims = claims_from_fields(spec, document)
    if context is not None and spec.wiki.guidance and not is_record:
        claims += await assess_document_for_wiki(session, document, context, client=client)
    return apply_claims(
        session, claims,
        document=None if is_record else document, record=document if is_record else None,
        operation=operation, links=links_from_fields(spec, document), retract_missing=True,
    )


async def withdraw_from_wiki(session: Session, source: Document | Record, *, description: str) -> IngestReport:
    """Everything this source asserted is superseded (or, where other sources
    still support a claim, only this source's link is withdrawn). Used when a
    document or record is re-filed."""
    is_record = isinstance(source, Record)
    return apply_claims(
        session, [], document=None if is_record else source, record=source if is_record else None,
        domain=source.domain, operation=WikiOperation.EDIT, description=description, retract_missing=True,
    )
