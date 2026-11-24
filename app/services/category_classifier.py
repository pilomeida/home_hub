"""Suggests which domain + category an incoming document belongs to.

Registry-driven: the prompt is built from each registered CategorySpec's
description and its categories' descriptions, so a new domain becomes
classifiable the moment it registers -- nothing here names a domain. This
only ever SUGGESTS; a human approves every document in the Inbox."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional, Sequence

from app.categories.registry import implemented_categories
from app.models.category import Category
from app.services.reading import FileTooLargeError
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
    # The rest of the guess (PLAN step 12b), checked by code: new_area / new_kind (marked new), who, who_is_family,
    # items [{name, room, new_room}], tags, act_by {date, what}, paper_date, what, filed_name.
    guess: dict = field(default_factory=dict)


MAX_ITEMS, MAX_TAGS = 20, 10

# No stricter than the prompt and the parser (.get with defaults for every field; nothing is indexed directly):
# the code check (checked_guess) decides what is kept.
_CATEGORY_SCHEMA = {
    "type": "object",
    "properties": {
        "domain": {"type": ["string", "null"]},
        "category": {"type": ["string", "null"]},
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "reason": {"type": ["string", "null"]},
        "new_domain": {"type": ["string", "null"]},
        "new_category": {"type": ["string", "null"]},
        "who": {"type": ["string", "null"]},
        "items": {"type": ["array", "null"]},
        "tags": {"type": ["array", "null"]},
        "act_by": {"type": ["object", "null"]},
        "paper_date": {"type": ["string", "null"]},
        "what": {"type": ["string", "null"]},
    },
}


def build_system_prompt(categories: Sequence, family: Sequence[str] = (), rooms: Sequence[str] = ()) -> str:
    lines = [
        "You sort household documents into the area of family life they belong to, and say what each one is.",
        "The sender's caption or email subject, when there is one, is the strongest clue: lean on it first for what the "
        "paper is, who it is about, its tags and its name; then read the paper itself.",
        "Choose exactly one domain and one of THAT domain's categories from this list:",
        "",
    ]
    for spec in categories:
        inferred = " (category may be null — this area works out its own category)" if spec.reading_proposes_kind else ""
        lines.append(f'- domain "{spec.category.value}" ({spec.label}): {spec.description}{inferred}')
        for kind in (c for c in spec.kinds if c.classifiable):
            lines.append(f'    - category "{kind.value}" ({kind.label}): {kind.description}')
    lines += [
        "",
        "Every paper has a type. If no domain fits, set domain and category to null and propose a new one: "
        "new_domain (a short English name for a new area, e.g. \"Work\") and new_category (a short English name for "
        "the kind of paper, e.g. \"Employment contract\"). If the domain fits but none of its categories does, give "
        "the domain, category null, and new_category.",
    ]
    if family:
        lines.append(f"The family: {', '.join(family)}. who: the person the paper is about, one of these when it is "
                     "one of them, else the name as printed; null when the paper is about the house or no one.")
    if rooms:
        lines.append(f"The rooms of the house: {', '.join(rooms)}. For a house paper (an invoice, warranty, manual), "
                     "items lists each physical thing it is about (not delivery, installation or fees), each with the "
                     "room it most likely goes in, one of these rooms when one fits.")
    lines += [
        "",
        "Respond with ONLY a JSON object:",
        '{"domain": "<domain id or null>", "category": "<category id or null>", '
        '"new_domain": "<name or null>", "new_category": "<name or null>", '
        '"confidence": <number 0.0-1.0>, "reason": "<one short sentence>", '
        '"who": "<name or null>", "items": [{"name": "...", "room": "<room or null>"}], '
        '"tags": ["<a few words that describe the paper: area, kind, level, grade, issuer...>"], '
        '"act_by": {"date": "YYYY-MM-DD", "what": "<what must be done or ends then>"} or null, '
        '"paper_date": "YYYY-MM-DD or null (the date the paper was issued or signed)", '
        '"what": "<what the paper is in at most 8 words, in its own language: issuer and subject, no amounts, no '
        'reference numbers, no names of people; e.g. \"Fatura AEG eletrodomesticos cozinha\">"}',
        "",
        "confidence is how sure you are that the whole answer is right.",
    ]
    return "\n".join(lines)


def _empty(note: str) -> CategorySuggestion:
    return CategorySuggestion(category=None, paper_kind=None, confidence=0.0, note=note)


def _text(value, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:limit] or None


def _day(value) -> Optional[str]:
    from datetime import date
    try:
        return date.fromisoformat(str(value)[:10]).isoformat() if value else None
    except ValueError:
        return None


def _household_lists() -> tuple[list[str], list[str]]:
    """The family and the rooms (S5), read from the registry's database; empty when it cannot be read."""
    from app.categories.registry import _open_session
    from app.services import household
    try:
        with _open_session() as session:
            return [s.name for s in household.family(session)], [s.name for s in household.rooms(session)]
    except Exception:          # no table yet, or no database: the guess still runs, without the lists
        return [], []


def _match(value: Optional[str], names: Sequence[str]) -> Optional[str]:
    from app.services.names import name_key
    if not value:
        return None
    key = name_key(value)
    for name in names:
        if name_key(name) == key or (key.split() and name_key(name) == key.split()[0]):
            return name
    return None


def checked_guess(data: dict, family: Sequence[str], rooms: Sequence[str]) -> dict:
    """Keep only what can be trusted (Q1): real dates, bounded lists, people and rooms matched to the lists."""
    who = _text(data.get("who"), 80)
    family_name = _match(who, family)
    items = []
    for raw in (data.get("items") or [])[:200]:
        if not isinstance(raw, dict) or not _text(raw.get("name"), 80):
            continue
        room = _text(raw.get("room"), 60)
        known = _match(room, rooms) if room else None
        items.append({"name": _text(raw.get("name"), 80), "room": known or room, "new_room": bool(room and not known)})
        if len(items) == MAX_ITEMS:
            break
    tags: list[str] = []
    for raw in data.get("tags") or []:
        tag = _text(raw, 40)
        if tag and tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
        if len(tags) == MAX_TAGS:
            break
    act = data.get("act_by") if isinstance(data.get("act_by"), dict) else {}
    act_day = _day(act.get("date"))
    return {"who": family_name or who, "who_is_family": family_name is not None, "items": items, "tags": tags,
            "act_by": {"date": act_day, "what": _text(act.get("what"), 80) or ""} if act_day else None,
            "paper_date": _day(data.get("paper_date")), "what": _text(data.get("what"), 120)}


async def suggest_category_and_kind(
    file_path: str,
    context_text: Optional[str] = None,
    *,
    categories: Optional[Sequence] = None,
    gateway=None,
    session=None,
) -> CategorySuggestion:
    from app.services import household
    specs = list(categories) if categories is not None else implemented_categories()
    if not is_model_readable(file_path):
        return _empty("This file type can't be read automatically — please choose where it goes.")

    gw = gateway or get_gateway()
    try:
        content = [build_content_block(file_path)]
    except FileTooLargeError:
        return _empty("This file is too big for the AI to read in one go — please choose where it goes.")
    if session is not None:
        family, rooms = [s.name for s in household.family(session)], [s.name for s in household.rooms(session)]
    else:
        family, rooms = _household_lists()
    user_text = "Classify this document as JSON."
    if context_text:
        user_text = f"Context from the sender (the strongest clue):\n{context_text}\n\n{user_text}"

    result = await gw.run(
        VISION_CLASSIFICATION,
        build_system_prompt(specs, family, rooms),
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
        guess = checked_guess(data, family, rooms)
    except (IndexError, AttributeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CategoryClassificationError(f"Could not parse classifier response: {exc}") from exc
    return _resolve(specs, category_value, kind_value, _text(data.get("new_domain"), 60),
                    _text(data.get("new_category"), 60), confidence, reason, guess)


def _resolve(specs, category_value, kind_value, new_domain, new_kind, confidence, reason, guess) -> CategorySuggestion:
    """Name the area and kind the guess points to; a new one is kept as new (never confident), unless it already
    exists under that name."""
    from datetime import datetime
    from app.services.paper_names import name_for_guess
    from app.services.paper_types import value_for
    spec = next((s for s in specs if s.category.value == category_value), None)
    if spec is None and new_domain:
        spec = next((s for s in specs if value_for(s.label) == value_for(new_domain)
                     or s.category.value == value_for(new_domain)), None)
        new_domain = None if spec is not None else new_domain
    if spec is None and category_value is not None:
        return _empty(f"Suggestion '{category_value}/{kind_value}' isn't a known area — please choose.")
    if spec is not None and kind_value is None and new_kind:
        known = next((k for k in spec.kinds if k.classifiable and (value_for(k.label) == value_for(new_kind)
                                                                   or k.value == value_for(new_kind))), None)
        kind_value, new_kind = (known.value, None) if known is not None else (None, new_kind)
    if spec is None:
        if not (new_domain and new_kind):
            return CategorySuggestion(None, None, 0.0, reason or "Didn't match any area.", guess)
        guess = {**guess, "new_area": new_domain, "new_kind": new_kind}
        guess["filed_name"] = name_for_guess(guess, new_kind, datetime.utcnow())
        return CategorySuggestion(None, None, min(confidence, CONFIDENT_THRESHOLD / 2), reason, guess)
    if kind_value is None and new_kind:
        guess = {**guess, "new_kind": new_kind}
        guess["filed_name"] = name_for_guess(guess, new_kind, datetime.utcnow())
        return CategorySuggestion(spec.category, None, min(confidence, CONFIDENT_THRESHOLD / 2), reason, guess)
    valid_category = (kind_value in {c.value for c in spec.kinds if c.classifiable}
                      or (kind_value is None and spec.reading_proposes_kind))
    if not valid_category:
        return _empty(f"Suggestion '{category_value}/{kind_value}' isn't a known area — please choose.")
    guess = {**guess, "filed_name": name_for_guess(guess, spec.kind_label(kind_value) if kind_value else spec.label,
                                                   datetime.utcnow())}
    return CategorySuggestion(category=spec.category, paper_kind=kind_value, confidence=confidence, note=reason,
                              guess=guess)
