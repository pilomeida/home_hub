"""[[ref]] markers -> numbered source links, per turn. Only refs the
engine actually saw (index, tool results, earlier turns) survive."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from app.models.ask import AskTurn
from app.services.ask.contracts import Citable

_MARKER = re.compile(r"\s*\[\[([a-z_]+:[A-Za-z0-9_:.\-]+)\]\]")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class Segment:
    text: str
    source_number: Optional[int] = None


@dataclass
class RenderedAnswer:
    paragraphs: list[list[Segment]] = field(default_factory=list)
    sources: list[tuple[int, Citable]] = field(default_factory=list)


def cited_refs(text: str, known: dict[str, Citable]) -> list[str]:
    seen: list[str] = []
    for ref in _MARKER.findall(text or ""):
        if ref in known and ref not in seen:
            seen.append(ref)
    return seen


def render_answer(text: str, known: dict[str, Citable]) -> RenderedAnswer:
    order = cited_refs(text, known)
    numbers = {ref: i + 1 for i, ref in enumerate(order)}
    rendered = RenderedAnswer(sources=[(numbers[r], known[r]) for r in order])
    for raw in _PARAGRAPH_BREAK.split((text or "").strip()):
        segments: list[Segment] = []
        pos = 0
        for match in _MARKER.finditer(raw):
            if match.start() > pos:
                segments.append(Segment(raw[pos:match.start()]))
            if match.group(1) in numbers:
                segments.append(Segment("", numbers[match.group(1)]))
            pos = match.end()
        if pos < len(raw):
            segments.append(Segment(raw[pos:]))
        if segments:
            rendered.paragraphs.append(segments)
    return rendered


def plain_text(text: str, known: dict[str, Citable]) -> str:
    def _replace(match: re.Match) -> str:
        ref = match.group(1)
        return f" ({known[ref].label})" if ref in known else ""
    return _MARKER.sub(_replace, text or "").strip()


def known_from_turn(turn: AskTurn) -> dict[str, Citable]:
    return {c["ref"]: Citable(**c) for c in json.loads(turn.citations_json or "[]")}


def render_turn(turn: AskTurn) -> RenderedAnswer:
    return render_answer(turn.answer_text or "", known_from_turn(turn))
