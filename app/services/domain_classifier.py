"""Suggests which domain + category an incoming document belongs to.

Registry-driven: the prompt is built from each registered DomainSpec's
description and its categories' descriptions, so a new domain becomes
classifiable the moment it registers -- nothing here names a domain. This
only ever SUGGESTS; a human approves every document in the Inbox."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Sequence

from anthropic import AsyncAnthropic

from app.config import settings
from app.domains.registry import implemented_domains
from app.models.domain import Domain
from app.services.document_input import build_content_block, is_model_readable
from app.services.json_utils import strip_json_fences

_MODEL = "claude-haiku-4-5-20251001"  # same cheap classifier model the Financials handler uses
CONFIDENT_THRESHOLD = 0.8


class DomainClassificationError(Exception):
    pass


@dataclass
class DomainSuggestion:
    domain: Optional[Domain]
    category: Optional[str]
    confidence: float
    note: str


def build_system_prompt(domains: Sequence) -> str:
    lines = [
        "You sort household documents into the area of family life they belong to.",
        "Choose exactly one domain and one of THAT domain's categories from this list:",
        "",
    ]
    for spec in domains:
        inferred = " (category may be null — this area works out its own category)" if spec.infers_category else ""
        lines.append(f'- domain "{spec.domain.value}" ({spec.label}): {spec.description}{inferred}')
        for category in spec.categories:
            lines.append(f'    - category "{category.value}" ({category.label}): {category.description}')
    lines += [
        "",
        "Respond with ONLY a JSON object:",
        '{"domain": "<domain id or null>", "category": "<category id or null>", '
        '"confidence": <number 0.0-1.0>, "reason": "<one short sentence>"}',
        "",
        "Use null for domain and category if the document fits none of them. "
        "confidence is how sure you are that the whole answer is right.",
    ]
    return "\n".join(lines)


def _empty(note: str) -> DomainSuggestion:
    return DomainSuggestion(domain=None, category=None, confidence=0.0, note=note)


async def suggest_domain_and_category(
    file_path: str,
    context_text: Optional[str] = None,
    *,
    domains: Optional[Sequence] = None,
    client: Optional[AsyncAnthropic] = None,
) -> DomainSuggestion:
    specs = list(domains) if domains is not None else implemented_domains()
    if not is_model_readable(file_path):
        return _empty("This file type can't be read automatically — please choose where it goes.")

    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    content: list[dict] = [build_content_block(file_path)]
    if context_text:
        content.append({"type": "text", "text": f"Context from the sender:\n{context_text}"})
    content.append({"type": "text", "text": "Classify this document as JSON."})

    message = await anthropic_client.messages.create(
        model=_MODEL,
        max_tokens=256,
        thinking={"type": "disabled"},
        system=build_system_prompt(specs),
        messages=[{"role": "user", "content": content}],
    )

    try:
        data = json.loads(strip_json_fences(message.content[0].text))
        domain_value = data.get("domain")
        category_value = data.get("category") or None
        confidence = min(max(float(data.get("confidence") or 0.0), 0.0), 1.0)
        reason = str(data.get("reason") or "")
    except (IndexError, AttributeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DomainClassificationError(f"Could not parse classifier response: {exc}") from exc

    if domain_value is None:
        return _empty(reason or "Didn't match any area.")
    spec = next((s for s in specs if s.domain.value == domain_value), None)
    valid_category = spec is not None and (
        category_value in {c.value for c in spec.categories}
        or (category_value is None and spec.infers_category)
    )
    if not valid_category:
        return _empty(f"Suggestion '{domain_value}/{category_value}' isn't a known area — please choose.")
    return DomainSuggestion(domain=spec.domain, category=category_value, confidence=confidence, note=reason)
