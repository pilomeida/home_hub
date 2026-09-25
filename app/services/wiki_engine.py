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
from app.domains.fields import load_fields
from app.domains.registry import get_spec
from app.models.document import Document
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


def claims_from_fields(spec: DomainSpec, document: Document) -> list[ClaimInput]:
    fields = {**load_fields(document), "category": spec.category_label(document.category)}
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
                claims.append(ClaimInput(page=page, key=fact.key, value=value, label=fact.label, policy=fact.policy))
    return claims


def links_from_fields(spec: DomainSpec, document: Document) -> list[LinkInput]:
    fields = load_fields(document)
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
    document: Document,
    *,
    context: Optional[str] = None,
    operation: WikiOperation = WikiOperation.INGEST,
    client: Optional[AsyncAnthropic] = None,
) -> IngestReport:
    """Always writes exactly one wiki_log entry (`operation`, INGEST by default), even when nothing
    changes; Plan C's lint relies on it to find never-ingested documents."""
    spec = get_spec(document.domain)
    claims = claims_from_fields(spec, document)
    if context is not None and spec.wiki.guidance:
        claims += await assess_document_for_wiki(session, document, context, client=client)
    return apply_claims(
        session, claims, document=document, operation=operation, links=links_from_fields(spec, document),
    )
