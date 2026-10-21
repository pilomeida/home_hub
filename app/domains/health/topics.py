"""Topics: the family's baseline of headings found in written reports (a tree). The AI proposes them while reading,
a person approves, renames or merges them; the same chapter in a later report lands on the same topic, so a person's
findings can be followed across years (Pedro, Phase 1B)."""

from __future__ import annotations

import difflib
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.domains.health.text import PATH_SEP, match_key
from app.models.document import Document
from app.models.health import HealthEntry, HealthMeasurement, HealthTopic
from app.problems import Problem

# Wrapper headings that frame a report without naming a subject: they are not topics.
DOCUMENT_SECTIONS: frozenset[str] = frozenset({
    "conclusoes", "conclusao", "impressao", "resultados", "resultado", "comentarios", "comentario", "relatorio",
    "descricao", "achados", "findings", "conclusions", "impression", "results", "report"})

SIMILAR_AT_LEAST = 0.88


class TopicError(Exception):
    def __init__(self, problem: Problem, status: int = 400):
        super().__init__(problem.text)
        self.problem = problem
        self.status = status


def gone(what: str) -> TopicError:
    return TopicError(Problem(what, "That topic no longer exists.", "Reload the page to see what is still there."), 404)


def topic_path(group_path: str) -> list[str]:
    """The printed heading path as topic names, without the wrapper headings that open it ('Conclusões › ...')."""
    names = [p.strip() for p in (group_path or "").split(PATH_SEP.strip()) if p.strip()]
    while names and match_key(names[0]) in DOCUMENT_SECTIONS:
        names.pop(0)
    return names


def _follow(session: Session, topic: HealthTopic) -> HealthTopic:
    seen = set()
    while topic.merged_into_id is not None and topic.id not in seen:
        seen.add(topic.id)
        nxt = session.get(HealthTopic, topic.merged_into_id)
        if nxt is None:
            break
        topic = nxt
    return topic


def _children(session: Session, parent_id: Optional[int]) -> list[HealthTopic]:
    cond = HealthTopic.parent_id.is_(None) if parent_id is None else HealthTopic.parent_id == parent_id
    return list(session.exec(select(HealthTopic).where(cond).order_by(HealthTopic.id)).all())


def _without_number(key: str) -> str:
    """The key without a final one- or two-character token ('segmento v1' -> 'segmento'): numbered siblings."""
    head, _, last = key.rpartition(" ")
    return head if head and len(last) <= 2 else key


def _most_similar(session: Session, key: str) -> Optional[int]:
    best_id, best = None, SIMILAR_AT_LEAST
    for other in session.exec(select(HealthTopic).where(HealthTopic.merged_into_id.is_(None)).order_by(HealthTopic.id)).all():
        if other.match_key != key and _without_number(other.match_key) == _without_number(key):
            continue                                       # 'Segmento V1' and 'Segmento V2' are siblings, not the same topic
        ratio = difflib.SequenceMatcher(None, key, other.match_key).ratio()
        if ratio >= best and (best_id is None or ratio > best):
            best_id, best = other.id, ratio
    return best_id


def resolve_topic(session: Session, path: list[str]) -> Optional[HealthTopic]:
    current: Optional[HealthTopic] = None
    for name in path:
        key = match_key(name)
        if not key:
            continue
        parent_id = current.id if current is not None else None
        same = [c for c in _children(session, parent_id) if c.match_key == key]
        found = next((c for c in same if c.merged_into_id is None), same[0] if same else None)     # a live one first
        if found is not None:
            current = _follow(session, found)
            continue
        topic = HealthTopic(name=name.strip(), match_key=key, parent_id=parent_id, approved=False,
                            similar_to_id=_most_similar(session, key))
        session.add(topic)
        session.commit()
        session.refresh(topic)
        current = topic
    return current


def attach_topics(session: Session, entry: HealthEntry) -> None:
    """Give each result of a written report the topic its headings name (none when it has no subject heading)."""
    if entry.layout != "report":
        return
    for m in session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)).all():
        topic = resolve_topic(session, topic_path(m.group_path))
        m.topic_id = topic.id if topic is not None else None
        session.add(m)
    session.commit()


def approve_topic(session: Session, topic_id: int) -> None:
    topic = session.get(HealthTopic, topic_id)
    if topic is None:
        raise gone("Not approved.")
    topic.approved = True
    session.add(topic)
    session.commit()


def rename_topic(session: Session, topic_id: int, new_name: str) -> None:
    topic = session.get(HealthTopic, topic_id)
    key = match_key(new_name)
    if topic is None or not key:
        raise TopicError(Problem("Not renamed.", "The new name is empty or the topic no longer exists.",
                                 "Type a name and try again."))
    clash = next((c for c in _children(session, topic.parent_id) if c.match_key == key and c.id != topic.id
                  and c.merged_into_id is None), None)
    if clash is not None:
        raise TopicError(Problem("Not renamed.", f"There is already a topic called {clash.name} in the same place.",
                                 "Use Merge to combine the two, or choose another name."))
    topic.name, topic.match_key = " ".join(new_name.split()), key
    session.add(topic)
    session.commit()


def _is_under(session: Session, topic: HealthTopic, ancestor_id: int) -> bool:
    seen = set()
    while topic.parent_id is not None and topic.id not in seen:
        if topic.parent_id == ancestor_id:
            return True
        seen.add(topic.id)
        nxt = session.get(HealthTopic, topic.parent_id)
        if nxt is None:
            return False
        topic = nxt
    return False


def _merge_into(session: Session, source: HealthTopic, target: HealthTopic) -> None:
    for m in session.exec(select(HealthMeasurement).where(HealthMeasurement.topic_id == source.id)).all():
        m.topic_id = target.id
        session.add(m)
    existing = {c.match_key: c for c in _children(session, target.id) if c.merged_into_id is None}
    for child in _children(session, source.id):
        if child.merged_into_id is not None:
            continue
        twin = existing.get(child.match_key)
        if twin is not None:
            _merge_into(session, child, twin)
        else:
            child.parent_id = target.id
            session.add(child)
            existing[child.match_key] = child
    source.merged_into_id, source.approved = target.id, True
    session.add(source)


def merge_topics(session: Session, source_id: int, target_id: int) -> None:
    source = session.get(HealthTopic, source_id)
    target = session.get(HealthTopic, target_id)
    if source is None or target is None:
        raise gone("Not merged.")
    if source.id == target.id:
        raise TopicError(Problem("Not merged.", f"{source.name} cannot be merged into itself.",
                                 "Pick a different topic to keep, then press Merge."))
    if source.merged_into_id is not None or target.merged_into_id is not None:
        gone_one = source if source.merged_into_id is not None else target
        raise TopicError(Problem("Not merged.", f"{gone_one.name} was already merged into another topic.",
                                 "Reload the page to see what is still listed, then try again."))
    if _is_under(session, target, source.id):
        raise TopicError(Problem("Not merged.", f"{target.name} sits inside {source.name}, so {source.name} cannot be folded into it.",
                                 f"Merge {target.name} into {source.name} instead, or pick a topic outside {source.name}."))
    _merge_into(session, source, target)
    session.commit()


def _full_path(session: Session, topic: HealthTopic) -> str:
    names, seen = [topic.name], {topic.id}
    while topic.parent_id is not None and topic.parent_id not in seen:
        parent = session.get(HealthTopic, topic.parent_id)
        if parent is None:
            break
        names.append(parent.name)
        seen.add(parent.id)
        topic = parent
    return PATH_SEP.join(reversed(names))


def names_for_prompt(session: Session, limit: int = 60) -> list[str]:
    """Paths the reader may reuse: a topic is offered only when it and every ancestor is approved and not merged away."""
    everyone = {t.id: t for t in session.exec(select(HealthTopic)).all()}

    def offered(topic: HealthTopic) -> bool:
        seen = set()
        while topic is not None and topic.id not in seen:
            if not topic.approved or topic.merged_into_id is not None:
                return False
            seen.add(topic.id)
            if topic.parent_id is None:
                return True
            topic = everyone.get(topic.parent_id)
        return False

    paths = sorted((_full_path(session, t) for t in everyone.values() if offered(t)), key=lambda p: (match_key(p), p))
    return paths[:limit]


def path_below(session: Session, topic_id: Optional[int], root_id: int, cache: Optional[dict] = None) -> str:
    """The names from just below `root_id` down to `topic_id` ('' for the root itself or a topic outside it)."""
    cache = {} if cache is None else cache
    names, seen = [], set()
    while topic_id is not None and topic_id != root_id and topic_id not in seen:
        seen.add(topic_id)
        if topic_id not in cache:
            cache[topic_id] = session.get(HealthTopic, topic_id)
        t = cache[topic_id]
        if t is None:
            break
        names.append(t.name)
        topic_id = t.parent_id
    return PATH_SEP.join(reversed(names))


def _subtree_ids(session: Session, root_id: int) -> list[int]:
    ids, queue = [root_id], [root_id]
    while queue:
        for child in _children(session, queue.pop()):
            if child.id not in ids:
                ids.append(child.id)
                queue.append(child.id)
    return ids


def people_of(session: Session, topic_id: int) -> list[tuple[int, int]]:
    """Who has results under a topic (merge followed, sub-topics included): (person id, number of results printed on
    their own papers, earlier restated results not counted), most results first, then by person id."""
    topic = session.get(HealthTopic, topic_id)
    if topic is None:
        raise gone("Nothing to show.")
    ids = _subtree_ids(session, _follow(session, topic).id)
    counts: dict[int, int] = {}
    for pid in session.exec(select(HealthMeasurement.person_id).where(
            HealthMeasurement.topic_id.in_(ids), HealthMeasurement.person_id.is_not(None),
            HealthMeasurement.is_previous.is_(False))).all():
        counts[pid] = counts.get(pid, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def thread(session: Session, topic_id: int, person_id: Optional[int]) -> dict:
    """One person's findings under a topic (and its sub-topics) across all their papers, newest first, with the
    numeric ones as series (earlier results printed on a later paper count as earlier points). With no person
    (nobody has results under the topic) the answer is the topic and its path with no findings."""
    topic = session.get(HealthTopic, topic_id)
    if topic is None:
        raise gone("Nothing to show.")
    topic = _follow(session, topic)
    if person_id is None:
        return {"topic": topic, "path": _full_path(session, topic), "entries": [], "metrics": []}
    ids = _subtree_ids(session, topic.id)
    rows = session.exec(select(HealthMeasurement).where(HealthMeasurement.topic_id.in_(ids),
                                                        HealthMeasurement.person_id == person_id)
                        .order_by(HealthMeasurement.position, HealthMeasurement.id)).all()
    by_entry: dict[int, list[HealthMeasurement]] = {}
    for m in rows:
        if not m.is_previous:
            by_entry.setdefault(m.entry_id, []).append(m)
    entries = []
    for entry_id, items in by_entry.items():
        entry = session.get(HealthEntry, entry_id)
        entries.append({"entry": entry, "document": session.get(Document, entry.document_id), "items": items})
    entries.sort(key=lambda e: (e["entry"].entry_date or date.min, e["entry"].id), reverse=True)

    entry_dates = {e["entry"].id: e["entry"].entry_date for e in entries}
    series: dict[tuple, dict] = {}
    names: dict[int, Optional[HealthTopic]] = {}
    for m in rows:
        if m.value is None:
            continue
        when = m.measured_on or (None if m.is_previous else entry_dates.get(m.entry_id))
        if when is None:
            continue
        # one series per topic and metric, however the paper spelled the headings above it or the metric's name
        s = series.setdefault((m.topic_id, match_key(m.test), match_key(m.unit or "")), {
            "label": m.test, "unit": m.unit or "", "points": set(), "newest": date.min,
            "caption": path_below(session, m.topic_id, topic.id, names)})
        s["points"].add((when, m.value))              # the same result restated on a later paper is one point
        if when >= s["newest"]:
            s["newest"], s["label"], s["unit"] = when, m.test, m.unit or ""      # named as the newest paper spells it
    metrics = [{"label": s["label"], "unit": s["unit"], "caption": s["caption"], "points": sorted(s["points"])}
               for _, s in sorted(series.items(), key=lambda kv: (match_key(kv[1]["caption"]), kv[0][1], kv[0][2], kv[0][0] or 0))]
    return {"topic": topic, "path": _full_path(session, topic), "entries": entries, "metrics": metrics}
