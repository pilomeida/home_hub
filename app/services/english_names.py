"""English names for the Health areas and the wiki topics already on file (PLAN step 6c; Pedro, 2026-11-19: areas,
topics and wiki guidance in English; each paper's own summary and wording stay in its language).

A Portuguese area joins its English twin (merged, so the Portuguese name stays an alias and still leads there); a
Portuguese condition gets an English entry it is merged into; a Portuguese topic page is renamed, its old title kept
as an alias. New names are English from the readers' prompts. The lists are the names found live on 2026-11-20
(Claude, 2026-11-20; Pedro may change a translation by renaming it on its page)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.categories.health import areas as areas_svc
from app.models.health import HealthArea
from app.models.wiki import TOPIC_PAGE_TYPE, WikiPage
from app.services.names import name_key
from app.services.wiki_store import rename_page

AREAS = {"Sangue": "Blood", "Rins e vias urinárias": "Kidneys and urinary system", "Fígado": "Liver",
         "Coração e circulação": "Heart and circulation", "Tiroide": "Thyroid", "Próstata": "Prostate"}
CONDITIONS = {"AVC isquémico": "Ischaemic stroke", "Placa ateromatosa": "Atheromatous plaque"}
TOPICS = {"Pedro: check-ups e análises": "Pedro: check-ups and tests",
          "Pedro: condições e diagnósticos": "Pedro: conditions and diagnoses",
          "Vicente: olhos": "Vicente: eyes"}


@dataclass
class Plan:
    merges: list[tuple[str, str, str]] = field(default_factory=list)     # (kind, Portuguese, English)
    renames: list[tuple[str, str]] = field(default_factory=list)         # (old title, new title)


def _live(session: Session, kind: str, name: str):
    area = session.exec(select(HealthArea).where(HealthArea.kind == kind,
                                                 HealthArea.match_key == areas_svc.match_key(name))).first()
    return area if area is not None and area.merged_into_id is None else None


def plan(session: Session) -> Plan:
    """What `apply` would do; nothing is changed."""
    out = Plan()
    for kind, names in (("area", AREAS), ("condition", CONDITIONS)):
        for pt, en in names.items():
            if _live(session, kind, pt) is not None:
                out.merges.append((kind, pt, en))
    for page in session.exec(select(WikiPage).where(WikiPage.page_type == TOPIC_PAGE_TYPE)).all():
        en = next((v for k, v in TOPICS.items() if name_key(k) == name_key(page.topic)), None)
        if en is not None:
            out.renames.append((page.topic, en))
    return out


def apply(session: Session) -> Plan:
    done = plan(session)
    for kind, pt, en in done.merges:
        source = _live(session, kind, pt)
        target = areas_svc.resolve_area(session, en, kind)          # the English twin, or a new English entry
        areas_svc.merge_areas(session, source.id, target.id)
    for old, new in done.renames:
        page = session.exec(select(WikiPage).where(WikiPage.topic == old)).one()
        rename_page(session, page, new)
    session.commit()
    return done


__all__ = ["AREAS", "CONDITIONS", "TOPICS", "Plan", "plan", "apply"]
