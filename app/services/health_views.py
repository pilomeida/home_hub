"""What the Health pages show of a paper and of the topic tree: a paper's results in its own order and grouping, a
written report's outline, the papers still waiting, the areas, the topics as a tree, and a topic's conclusions.
Presentation only: nothing here changes what is stored."""

from typing import Optional

from sqlmodel import Session, select

from app.categories.health import topics as topics_svc
from app.categories.health.text import PATH_SEP, match_key, reference_for_display
from app.models.category import Category
from app.models.document import Document, DocumentStatus
from app.models.health import HealthArea, HealthEntry, HealthMeasurement, HealthTopic


def measurements_of(session: Session, entry: HealthEntry) -> list[HealthMeasurement]:
    return list(session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)
                             .order_by(HealthMeasurement.position, HealthMeasurement.is_previous,
                                       HealthMeasurement.measured_on)))


def papers_waiting(session: Session) -> list[Document]:
    """Health papers not read yet or that need someone's attention, oldest first."""
    return list(session.exec(select(Document).where(
        Document.category == Category.HEALTH,
        Document.status.in_([DocumentStatus.PENDING, DocumentStatus.NEEDS_ATTENTION])).order_by(Document.id)))


def live_areas(session: Session) -> list[HealthArea]:
    return list(session.exec(select(HealthArea).where(HealthArea.merged_into_id.is_(None))
                             .order_by(HealthArea.approved, HealthArea.kind, HealthArea.name)))


def measurement_view(rows: list[HealthMeasurement]) -> list[dict]:
    """The paper's results in the paper's own order and grouping: before a result, the headings (outermost first)
    that differ from the previous result's; after its value, the earlier results the paper printed for it."""
    earlier: dict[int, list[HealthMeasurement]] = {}
    for m in rows:
        if m.is_previous:
            earlier.setdefault(m.position, []).append(m)
    view: list[dict] = []
    last: list[str] = []
    for m in rows:
        if m.is_previous:
            continue
        path = [h for h in m.group_path.split(PATH_SEP) if h] if m.group_path else []
        common = 0
        while common < len(path) and common < len(last) and path[common] == last[common]:
            common += 1
        flag = None
        if m.out_of_range and m.value is not None:
            flag = "high" if (m.ref_high is not None and m.value > m.ref_high) else "low"
        view.append({"m": m, "flag": flag, "headings": [(depth, path[depth]) for depth in range(common, len(path))],
                     "earlier": earlier.get(m.position, []),
                     "reference": reference_for_display(m.reference_text, m.ref_low, m.ref_high)})
        last = path
    return view


def heading_topics(session: Session, row: dict, cache: dict[int, Optional[HealthTopic]]) -> list[Optional[int]]:
    """For each heading shown above a result, the topic it links to (None: no link). The result's own (last) heading
    links to the result's topic, even after a merge. A heading above it links to the result's topic's ancestor at that
    level only when that ancestor still carries the printed name: after a merge the ancestors are not the headings
    printed on this paper. A wrapper heading such as "Conclusões" is not a topic."""
    m = row["m"]
    path = [h for h in m.group_path.split(PATH_SEP) if h] if m.group_path else []
    if m.topic_id is None:
        return [None for _ in row["headings"]]
    chain: list[Optional[HealthTopic]] = []
    topic_id, seen = m.topic_id, set()
    while topic_id is not None and topic_id not in seen and len(chain) < len(topics_svc.topic_path(m.group_path)):
        seen.add(topic_id)
        if topic_id not in cache:
            cache[topic_id] = session.get(HealthTopic, topic_id)
        topic = cache[topic_id]
        chain.append(topic)
        topic_id = topic.parent_id if topic is not None else None
    ids: list[Optional[int]] = []
    for depth, name in row["headings"]:
        back = len(path) - 1 - depth                    # 0 = the result's own (last) heading
        topic = chain[back] if back < len(chain) else None
        if topic is not None and (back == 0 or match_key(name) == topic.match_key):
            ids.append(topic.id)
        else:
            ids.append(None)
    return ids


def outline_view(session: Session, entry: HealthEntry, view: list[dict]) -> list[dict]:
    """A written report's outline: its headings in the paper's order (each with the topic it names) and the prose
    under them. Numbers are not here: they go to the Metrics table."""
    cache: dict[int, Optional[HealthTopic]] = {}
    items: list[dict] = []
    for row in view:
        ids = heading_topics(session, row, cache)
        for (depth, name), topic_id in zip(row["headings"], ids):
            items.append({"kind": "heading", "depth": depth, "name": name, "topic_id": topic_id})
        m = row["m"]
        if m.value is None and m.value_text:
            label = None if match_key(m.test) in topics_svc.DOCUMENT_SECTIONS else m.test
            items.append({"kind": "prose", "m": m, "label": label})
    return items


def topic_rows(session: Session) -> list[dict]:
    """Every topic still in the baseline as a tree in reading order (roots first, then each one's children), with its
    depth, its full path, what it "looks like", and the topics it may be merged into (not itself or what is inside it)."""
    live = list(session.exec(select(HealthTopic).where(HealthTopic.merged_into_id.is_(None))))
    by_id = {t.id: t for t in live}
    children: dict[Optional[int], list[HealthTopic]] = {}
    for t in live:
        children.setdefault(t.parent_id if t.parent_id in by_id else None, []).append(t)
    for kids in children.values():
        kids.sort(key=lambda t: (t.match_key, t.id))
    rows: list[dict] = []

    def walk(parent_id: Optional[int], depth: int, path: str) -> None:
        for t in children.get(parent_id, []):
            here = f"{path}{PATH_SEP}{t.name}" if path else t.name
            start = len(rows)
            rows.append({"t": t, "depth": depth, "path": here, "inside": set()})
            walk(t.id, depth + 1, here)
            rows[start]["inside"] = {r["t"].id for r in rows[start + 1:]}

    walk(None, 0, "")
    paths = {r["t"].id: r["path"] for r in rows}
    for r in rows:
        similar = by_id.get(r["t"].similar_to_id) if r["t"].similar_to_id is not None else None
        r["similar"] = ({"id": similar.id, "name": similar.name, "path": paths[similar.id]}
                        if similar is not None and similar.id != r["t"].id and similar.id not in r["inside"] else None)
        r["targets"] = [{"id": o["t"].id, "path": o["path"]} for o in rows
                        if o["t"].id != r["t"].id and o["t"].id not in r["inside"]]
    return rows


def is_prose(m: HealthMeasurement) -> bool:
    return m.value is None and bool(m.value_text)


def conclusions(session: Session, topic: HealthTopic, entries: list[dict]) -> list[dict]:
    """The thread's papers (already newest first) with their prose grouped under the sub-topic it belongs to."""
    cache: dict[int, Optional[HealthTopic]] = {}

    def below(topic_id: Optional[int]) -> str:
        return topics_svc.path_below(session, topic_id, topic.id, cache)

    out = []
    for e in entries:
        prose = [{"sub": below(m.topic_id), "m": m} for m in e["items"] if is_prose(m)]
        if prose:
            out.append({"entry": e["entry"], "document": e["document"], "prose": prose})
    return out
