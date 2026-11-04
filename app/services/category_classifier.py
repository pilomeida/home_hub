"""Suggests which domain + category an incoming document belongs to.

Registry-driven: the prompt is built from each registered CategorySpec's
description and its categories' descriptions, so a new domain becomes
classifiable the moment it registers -- nothing here names a domain. This
only ever SUGGESTS; a human approves every document in the Inbox."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Sequence

from app.categories.registry import implemented_categories
from app.models.category import Category
from app.services.document_input import build_content_block, is_model_readable
from app.services.json_utils import strip_json_fences
from app.llm_gateway import VISION_CLASSIFICATION, get_gateway

CONFIDENT_THRESHOLD = 0.8


class CategoryClassificationError(Exception):
    pass


@dataclass
class CategorySuggestion:
    category: Optional[Category]
    paper_kind: Optional[str]
    confidence: float
    note: str


def build_system_prompt(categories: Sequence) -> str:
    lines = [
        "You sort household documents into the area of family life they belong to.",
        "Choose exactly one domain and one of THAT domain's categories from this list:",
        "",
    ]
    for spec in categories:
        inferred = " (category may be null — this area works out its own category)" if spec.infers_kind else ""
        lines.append(f'- domain "{spec.category.value}" ({spec.label}): {spec.description}{inferred}')
        for kind in (c for c in spec.kinds if c.classifiable):
            lines.append(f'    - category "{kind.value}" ({kind.label}): {kind.description}')
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
_CATEGORY_SCHEMA = {
    "type": "object",
    "properties": {
        "domain": {"type": ["string", "null"]},
        "category": {"type": ["string", "null"]},
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "reason": {"type": ["string", "null"]},
    },
}


def _empty(note: str) -> CategorySuggestion:
    return CategorySuggestion(category=None, paper_kind=None, confidence=0.0, note=note)


async def suggest_category_and_kind(
    file_path: str,
    context_text: Optional[str] = None,
    *,
    categories: Optional[Sequence] = None,
    gateway=None,
) -> CategorySuggestion:
    specs = list(categories) if categories is not None else implemented_categories()
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
        response_schema=_CATEGORY_SCHEMA,
    )

    try:
        data = json.loads(strip_json_fences(result.text))
        category_value = data.get("domain")
        kind_value = data.get("category") or None
        confidence = min(max(float(data.get("confidence") or 0.0), 0.0), 1.0)
        reason = str(data.get("reason") or "")
    except (IndexError, AttributeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CategoryClassificationError(f"Could not parse classifier response: {exc}") from exc

    if category_value is None:
        return _empty(reason or "Didn't match any area.")
    spec = next((s for s in specs if s.category.value == category_value), None)
    valid_category = spec is not None and (
        kind_value in {c.value for c in spec.kinds if c.classifiable}
        or (kind_value is None and spec.infers_kind)
    )
    if not valid_category:
        return _empty(f"Suggestion '{category_value}/{kind_value}' isn't a known area — please choose.")
    return CategorySuggestion(category=spec.category, paper_kind=kind_value, confidence=confidence, note=reason)
