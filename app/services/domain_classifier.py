"""Suggests which domain + category an incoming document belongs to.

Registry-driven: the prompt is built from each registered DomainSpec's
description and its categories' descriptions, so a new domain becomes
classifiable the moment it registers -- nothing here names a domain. This
only ever SUGGESTS; a human approves every document in the Inbox."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Sequence

from app.domains.registry import implemented_domains
from app.models.domain import Domain
from app.services.document_input import build_content_block, is_model_readable
from app.services.json_utils import strip_json_fences
from app.llm_gateway import VISION_CLASSIFICATION, get_gateway

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
        for category in (c for c in spec.categories if c.classifiable):
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


# No stricter than the prompt ("Use null for domain and category") and the
# parser (.get with defaults for every field — nothing is indexed directly).
_DOMAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "domain": {"type": ["string", "null"]},
        "category": {"type": ["string", "null"]},
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "reason": {"type": ["string", "null"]},
    },
}


def _empty(note: str) -> DomainSuggestion:
    return DomainSuggestion(domain=None, category=None, confidence=0.0, note=note)


async def suggest_domain_and_category(
    file_path: str,
    context_text: Optional[str] = None,
    *,
    domains: Optional[Sequence] = None,
    gateway=None,
) -> DomainSuggestion:
    specs = list(domains) if domains is not None else implemented_domains()
    if not is_model_readable(file_path):
        return _empty("This file type can't be read automatically — please choose where it goes.")

    gw = gateway or get_gateway()
    content = [build_content_block(file_path)]
    user_text = "Classify this document as JSON."
    if context_text:
        user_text = f"Context from the sender:\n{context_text}\n\n{user_text}"

    result = await gw.run(
        VISION_CLASSIFICATION,
        build_system_prompt(specs),
        user_text,
        attachments=content,
        response_schema=_DOMAIN_SCHEMA,
    )

    try:
        data = json.loads(strip_json_fences(result.text))
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
        category_value in {c.value for c in spec.categories if c.classifiable}
        or (category_value is None and spec.infers_category)
    )
    if not valid_category:
        return _empty(f"Suggestion '{domain_value}/{category_value}' isn't a known area — please choose.")
    return DomainSuggestion(domain=spec.domain, category=category_value, confidence=confidence, note=reason)
