"""What the reader is told before it reads a paper: the topics already in the family's baseline, and the outlines a
person confirmed as right for a provider. Guidance only: the paper's own structure always wins."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.domains.health import topics as topics_svc
from app.domains.health.text import PATH_SEP, match_key
from app.models.health import HealthEntry, HealthMeasurement, HealthOutlineProfile
from app.problems import Problem

_TOPICS_HEADER = ("Topics already in the family's baseline (reuse a name exactly when the paper has that heading; "
                  "the paper's own structure always wins):")


def _paths(outline_json: str) -> list[str]:
    try:
        items = json.loads(outline_json or "[]")
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    return [p for p in (PATH_SEP.join(str(x) for x in i) if isinstance(i, (list, tuple)) else str(i).strip() for i in items) if p]


def guidance(session: Session, limit: int = 6, max_chars: int = 3000) -> str:
    """The baseline topics and the most recent confirmed outlines as prompt text; "" when there are none."""
    blocks: list[list[str]] = []
    names = topics_svc.names_for_prompt(session)
    if names:
        blocks.append([_TOPICS_HEADER] + [f"- {n}" for n in names])
    profiles = session.exec(select(HealthOutlineProfile).order_by(
        HealthOutlineProfile.confirmed_at.desc(), HealthOutlineProfile.id.desc()).limit(limit)).all()
    for profile in profiles:
        paths = _paths(profile.outline_json)
        if paths:
            blocks.append([f"Outline a person confirmed as right for {profile.provider}:"] + [f"- {p}" for p in paths])

    lines: list[str] = []
    for block in blocks:
        if lines:
            lines.append("")
        lines += block
    kept: list[str] = []
    size = 0
    for line in lines:                                  # whole lines only, within the budget
        size += len(line) + (1 if kept else 0)
        if size > max_chars:
            break
        kept.append(line)
    while kept and (kept[-1] == "" or not kept[-1].startswith("- ")):      # no dangling blank or header without a path
        kept.pop()
    return "\n".join(kept)


class OutlineError(Exception):
    def __init__(self, problem: Problem, status: int = 400):
        super().__init__(problem.text)
        self.problem = problem
        self.status = status


def confirm_outline(session: Session, entry: HealthEntry, by: Optional[str]) -> HealthOutlineProfile:
    """A person confirms this paper's outline as right: it becomes the provider's profile, replacing an earlier one,
    and guides the later readings of that provider's papers."""
    provider = (entry.provider or "").strip()
    if not provider:
        raise OutlineError(Problem("Outline not confirmed.", "This paper does not say who issued it, so there is no one to keep the outline for.",
                                   "Fill in the provider on the paper, then confirm the outline again."), 409)
    rows = session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)
                        .order_by(HealthMeasurement.position, HealthMeasurement.id)).all()
    paths: list[str] = []
    for m in rows:
        if not m.is_previous and m.group_path and m.group_path not in paths:
            paths.append(m.group_path)
    if not paths:
        raise OutlineError(Problem("Outline not confirmed.", "No results with headings were read from this paper, so it has no outline.",
                                   "Read the paper again, or correct it by hand, then confirm the outline."), 409)
    layout = entry.layout or "sheet"
    key = match_key(provider)
    profile = session.exec(select(HealthOutlineProfile).where(
        HealthOutlineProfile.provider_key == key, HealthOutlineProfile.layout == layout)).first()
    if profile is None:
        profile = HealthOutlineProfile(provider_key=key, provider=provider, layout=layout, outline_json="[]")
    now = datetime.utcnow()
    profile.provider, profile.outline_json = provider, json.dumps(paths, ensure_ascii=False)
    profile.confirmed_by, profile.confirmed_at = by, now
    entry.outline_confirmed_by, entry.outline_confirmed_at = by, now
    session.add(profile)
    session.add(entry)
    session.commit()
    session.refresh(profile)
    return profile
