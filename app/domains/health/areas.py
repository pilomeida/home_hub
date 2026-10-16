"""Areas (eyes, skin, digestive system...) and conditions (stroke, diabetes...). The list is not preset: the
AI proposes names while reading Pedro's papers, a person approves, renames or merges them (Pedro, Q10)."""

from __future__ import annotations

from sqlmodel import Session, select

from app.domains.health.text import match_key
from app.models.health import HealthArea, HealthEntry, HealthEntryTag
from app.problems import Problem

KINDS = ("area", "condition")


class AreaError(Exception):
    def __init__(self, problem: Problem, status: int = 400):
        super().__init__(problem.text)
        self.problem = problem
        self.status = status


def gone(what: str) -> AreaError:
    return AreaError(Problem(what, "That entry no longer exists.", "Reload the page to see what is still there."), 404)


def _follow(session: Session, area: HealthArea) -> HealthArea:
    seen = set()
    while area.merged_into_id is not None and area.id not in seen:
        seen.add(area.id)
        nxt = session.get(HealthArea, area.merged_into_id)
        if nxt is None:
            break
        area = nxt
    return area


def resolve_area(session: Session, name: str, kind: str) -> HealthArea:
    key = match_key(name)
    existing = session.exec(select(HealthArea).where(HealthArea.kind == kind, HealthArea.match_key == key)).first()
    if existing is not None:
        return _follow(session, existing)
    area = HealthArea(name=name.strip(), kind=kind, match_key=key, approved=False)
    session.add(area)
    session.commit()
    session.refresh(area)
    return area


def tags_of(session: Session, entry_id: int) -> list[HealthArea]:
    rows = session.exec(select(HealthArea).join(HealthEntryTag, HealthEntryTag.area_id == HealthArea.id)
                        .where(HealthEntryTag.entry_id == entry_id).order_by(HealthArea.kind, HealthArea.name)).all()
    return list(rows)


def set_tags(session: Session, entry: HealthEntry, areas: list[str], conditions: list[str]) -> None:
    wanted: dict[int, HealthArea] = {}
    for names, kind in ((areas, "area"), (conditions, "condition")):
        for name in names:
            if match_key(name):
                a = resolve_area(session, name, kind)
                wanted[a.id] = a
    for tag in session.exec(select(HealthEntryTag).where(HealthEntryTag.entry_id == entry.id)).all():
        if tag.area_id not in wanted:
            session.delete(tag)
    have = {t.area_id for t in session.exec(select(HealthEntryTag).where(HealthEntryTag.entry_id == entry.id)).all()}
    for area_id in wanted:
        if area_id not in have:
            session.add(HealthEntryTag(entry_id=entry.id, area_id=area_id))
    session.commit()


def approve_area(session: Session, area_id: int) -> None:
    area = session.get(HealthArea, area_id)
    if area is None:
        raise gone("Not approved.")
    area.approved = True
    session.add(area)
    session.commit()


def rename_area(session: Session, area_id: int, new_name: str) -> None:
    area = session.get(HealthArea, area_id)
    key = match_key(new_name)
    if area is None or not key:
        raise AreaError(Problem("Not renamed.", "The new name is empty or the area no longer exists.",
                                "Type a name and try again."))
    clash = session.exec(select(HealthArea).where(HealthArea.kind == area.kind, HealthArea.match_key == key,
                                                  HealthArea.id != area.id)).first()
    if clash is not None:
        raise AreaError(Problem("Not renamed.", f"There is already an area called {clash.name}.",
                                "Use Merge to combine the two, or choose another name."))
    area.name, area.match_key = new_name.strip(), key
    session.add(area)
    session.commit()


def merge_areas(session: Session, source_id: int, target_id: int) -> None:
    source = session.get(HealthArea, source_id)
    target = session.get(HealthArea, target_id)
    if source is None or target is None:
        raise gone("Not merged.")
    if source.id == target.id:
        raise AreaError(Problem("Not merged.", f"{source.name} cannot be merged into itself.",
                                "Pick a different entry to keep, then press Merge."))
    if source.kind != target.kind:
        raise AreaError(Problem("Not merged.", "An area and a condition are separate lists and cannot be combined.",
                                "Pick an entry of the same kind, then press Merge."))
    if source.merged_into_id is not None or target.merged_into_id is not None:
        gone_one = source if source.merged_into_id is not None else target
        raise AreaError(Problem("Not merged.", f"{gone_one.name} was already merged into another entry.",
                                "Reload the page to see what is still listed, then try again."))
    for tag in session.exec(select(HealthEntryTag).where(HealthEntryTag.area_id == source_id)).all():
        exists = session.get(HealthEntryTag, (tag.entry_id, target_id))
        session.delete(tag)
        if exists is None:
            session.add(HealthEntryTag(entry_id=tag.entry_id, area_id=target_id))
    source.merged_into_id, source.approved = target_id, True
    session.add(source)
    session.commit()


def names_for_prompt(session: Session, kind: str) -> list[str]:
    rows = session.exec(select(HealthArea).where(HealthArea.kind == kind, HealthArea.merged_into_id.is_(None),
                                                 HealthArea.approved.is_(True)).order_by(HealthArea.name)).all()
    return [a.name for a in rows]
