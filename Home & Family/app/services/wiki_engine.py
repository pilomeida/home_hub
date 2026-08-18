"""Second-pass LLM assessment: decides whether an ingested document should
update a WikiPage, and applies the update if so."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from anthropic import AsyncAnthropic
from sqlmodel import Session, select

from app.config import settings
from app.models.document import Document
from app.models.transaction import Transaction
from app.models.wiki import WikiChange, WikiPage
from app.services.json_utils import strip_json_fences

_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = """You decide whether a bill/statement contains a standing \
fact worth remembering (e.g. current provider, contract/policy number, \
tariff, renewal date) as opposed to purely transactional data (an amount \
due this month). Respond with ONLY a JSON object:

{
  "wiki_worthy": true or false,
  "topic": "short human title, e.g. 'Electricity — provider & contract'",
  "facts": {"key": "value", ...}
}

If not wiki-worthy, set wiki_worthy to false and leave topic/facts empty."""


async def assess_and_update_wiki(
    session: Session,
    document: Document,
    transaction: Transaction,
    client: Optional[AsyncAnthropic] = None,
) -> Optional[WikiChange]:
    """Ask Claude whether this transaction/document contains wiki-worthy
    facts, and if so, upsert the relevant WikiPage and record a WikiChange."""
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = await anthropic_client.messages.create(
        model=_MODEL,
        max_tokens=512,
        thinking={"type": "disabled"},
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Provider: {transaction.provider}\n"
                    f"Category: {transaction.category.value}\n"
                    f"Statement period: {transaction.statement_period}\n"
                ),
            }
        ],
    )
    data = json.loads(strip_json_fences(message.content[0].text))
    if not data.get("wiki_worthy"):
        return None

    topic = data["topic"]
    new_facts: dict = data.get("facts", {})

    page = session.exec(select(WikiPage).where(WikiPage.topic == topic)).first()
    if page is None:
        page = WikiPage(topic=topic, facts_json=json.dumps(new_facts))
        session.add(page)
        session.commit()
        session.refresh(page)
        change = WikiChange(
            wiki_page_id=page.id, fact_key="*", old_value=None,
            new_value=json.dumps(new_facts), document_id=document.id,
        )
        session.add(change)
        session.commit()
        session.refresh(change)
        return change

    old_facts: dict = json.loads(page.facts_json)
    merged_facts = {**old_facts, **new_facts}
    changed_keys = {k: v for k, v in new_facts.items() if old_facts.get(k) != v}
    if not changed_keys:
        return None

    page.facts_json = json.dumps(merged_facts)
    page.updated_at = datetime.utcnow()
    session.add(page)

    change = WikiChange(
        wiki_page_id=page.id,
        fact_key=",".join(changed_keys.keys()),
        old_value=json.dumps({k: old_facts.get(k) for k in changed_keys}),
        new_value=json.dumps(changed_keys),
        document_id=document.id,
    )
    session.add(change)
    session.commit()
    session.refresh(change)
    return change
