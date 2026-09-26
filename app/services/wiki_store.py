"""Knowledge-layer storage: HOW the wiki is stored. Pure database work, no
LLM (app.services.wiki_engine decides WHAT to record).

Pages are made of claims; every claim links to the Document(s) asserting
it; a changed claim supersedes the old one (never deleted); every call to
apply_claims appends one entry to the wiki log; the index is generated.
Plan C's Query (filing answers back) and Lint also go through here."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional, Sequence

from sqlmodel import Session, select

from app.domains.base import FactPolicy
from app.domains.registry import get_spec, is_implemented
from app.models.document import Document
from app.models.domain import Domain
from app.models.record import Record
from app.models.wiki import (
    ANSWER_PAGE_TYPE, TOPIC_PAGE_TYPE, ClaimStatus, WikiClaim, WikiClaimSource, WikiLink, WikiLogEntry,
    WikiOperation, WikiPage,
)
from app.services.presentation import humanize_key

_SUMMARY_MAX = 160


def normalize_entity_key(name: str) -> str:
    return " ".join(name.lower().split())


@dataclass(frozen=True)
class PageRef:
    page_type: str
    title: str
    entity_key: Optional[str] = None
    summary: Optional[str] = None


@dataclass(frozen=True)
class ClaimInput:
    page: PageRef
    key: str
    value: str
    label: Optional[str] = None
    policy: FactPolicy = FactPolicy.REPLACE
    note: Optional[str] = None


@dataclass(frozen=True)
class LinkInput:
    from_page: PageRef
    to_page: PageRef
    bidirectional: bool = True


@dataclass
class IngestReport:
    page_ids: list[int] = field(default_factory=list)
    added: int = 0
    superseded: int = 0
    confirmed: int = 0
    links_added: int = 0
    retracted: int = 0
    log_entry_id: Optional[int] = None


def _candidate_titles(ref: PageRef) -> list[str]:
    return [ref.title, f"{ref.title} ({ref.page_type})"]


def find_page(session: Session, ref: PageRef) -> Optional[WikiPage]:
    if ref.entity_key is not None:
        return session.exec(
            select(WikiPage).where(WikiPage.page_type == ref.page_type, WikiPage.entity_key == ref.entity_key)
        ).first()
    return session.exec(
        select(WikiPage).where(
            WikiPage.page_type == ref.page_type,
            WikiPage.entity_key.is_(None),
            WikiPage.topic.in_(_candidate_titles(ref)),
        )
    ).first()


def _get_or_create_page(session: Session, ref: PageRef, domain: Optional[Domain]) -> WikiPage:
    page = find_page(session, ref)
    if page is not None:
        return page
    taken = set(session.exec(select(WikiPage.topic).where(WikiPage.topic.in_(_candidate_titles(ref)))).all())
    title = next((t for t in _candidate_titles(ref) if t not in taken), None)
    if title is None:
        raise ValueError(f"Wiki title collision for {ref.title!r} ({ref.page_type})")
    page = WikiPage(topic=title, page_type=ref.page_type, entity_key=ref.entity_key,
                    summary=ref.summary, domain=domain)
    session.add(page)
    session.flush()
    return page


def ensure_page(session: Session, ref: PageRef, domain: Optional[Domain]) -> WikiPage:
    return _get_or_create_page(session, ref, domain)


def add_link(session: Session, from_page_id: int, to_page_id: int, *, commit: bool = True) -> bool:
    if from_page_id == to_page_id:
        return False
    existing = session.exec(
        select(WikiLink).where(WikiLink.from_page_id == from_page_id, WikiLink.to_page_id == to_page_id)
    ).first()
    if existing is not None:
        return False
    session.add(WikiLink(from_page_id=from_page_id, to_page_id=to_page_id))
    if commit:
        session.commit()
    else:
        session.flush()
    return True


def links_from(session: Session, page_id: int) -> list[WikiPage]:
    return list(session.exec(
        select(WikiPage).join(WikiLink, WikiLink.to_page_id == WikiPage.id)
        .where(WikiLink.from_page_id == page_id).order_by(WikiPage.topic)
    ).all())


def links_to(session: Session, page_id: int) -> list[WikiPage]:
    return list(session.exec(
        select(WikiPage).join(WikiLink, WikiLink.from_page_id == WikiPage.id)
        .where(WikiLink.to_page_id == page_id).order_by(WikiPage.topic)
    ).all())


def active_claims(session: Session, page_id: int) -> list[WikiClaim]:
    return list(session.exec(
        select(WikiClaim).where(WikiClaim.page_id == page_id, WikiClaim.status == ClaimStatus.ACTIVE)
        .order_by(WikiClaim.id)
    ).all())


def superseded_claims(session: Session, page_id: int) -> list[WikiClaim]:
    return list(session.exec(
        select(WikiClaim).where(WikiClaim.page_id == page_id, WikiClaim.status == ClaimStatus.SUPERSEDED)
        .order_by(WikiClaim.superseded_at.desc(), WikiClaim.id.desc())
    ).all())


def _active_claim(session: Session, page_id: int, key: str) -> Optional[WikiClaim]:
    return session.exec(
        select(WikiClaim).where(
            WikiClaim.page_id == page_id, WikiClaim.key == key, WikiClaim.status == ClaimStatus.ACTIVE,
        )
    ).first()


def _source_filter(document: Optional[Document], record: Optional[Record]):
    if record is not None:
        return WikiClaimSource.record_id == record.id
    return WikiClaimSource.document_id == document.id


def _active_links(session: Session, claim_id: int) -> list[WikiClaimSource]:
    return list(session.exec(
        select(WikiClaimSource).where(WikiClaimSource.claim_id == claim_id, WikiClaimSource.withdrawn_at.is_(None))
    ).all())


def _has_source(session: Session, claim_id: int, document: Optional[Document], record: Optional[Record]) -> bool:
    return any(
        (record is not None and link.record_id == record.id) or (record is None and link.document_id == document.id)
        for link in _active_links(session, claim_id)
    )


def _add_source(session: Session, claim_id: int, document: Optional[Document], record: Optional[Record]) -> None:
    link = session.exec(
        select(WikiClaimSource).where(WikiClaimSource.claim_id == claim_id, _source_filter(document, record))
    ).first()
    if link is None:
        session.add(WikiClaimSource(claim_id=claim_id, document_id=None if record else document.id,
                                    record_id=record.id if record else None))
    elif link.withdrawn_at is not None:
        link.withdrawn_at = None
        session.add(link)
    session.flush()


def sources_for_claims(session: Session, claim_ids: Sequence[int]) -> dict[int, list[Document]]:
    if not claim_ids:
        return {}
    rows = session.exec(
        select(WikiClaimSource.claim_id, Document)
        .join(Document, Document.id == WikiClaimSource.document_id)
        .where(WikiClaimSource.claim_id.in_(list(claim_ids)), WikiClaimSource.withdrawn_at.is_(None))
        .order_by(WikiClaimSource.id)
    ).all()
    result: dict[int, list[Document]] = {}
    for claim_id, document in rows:
        result.setdefault(claim_id, []).append(document)
    return result


def claim_sources(session: Session, claim_ids: Sequence[int]) -> dict[int, list[Document | Record]]:
    if not claim_ids:
        return {}
    links = session.exec(
        select(WikiClaimSource).where(WikiClaimSource.claim_id.in_(list(claim_ids)), WikiClaimSource.withdrawn_at.is_(None))
        .order_by(WikiClaimSource.id)
    ).all()
    result: dict[int, list[Document | Record]] = {}
    for link in links:
        source = session.get(Record, link.record_id) if link.record_id else session.get(Document, link.document_id)
        result.setdefault(link.claim_id, []).append(source)
    return result


def _record_source_label(domain: Optional[Domain], record: Record) -> str:
    """A hand-entered Record's default log-description source, e.g.
    "Maintenance log (entered by hand) #1" -- the registry's category
    label, not the raw internal category key."""
    category_label = record.category
    if domain is not None and is_implemented(domain):
        category_label = get_spec(domain).category_label(record.category) or record.category
    return f"{category_label} (entered by hand) #{record.id}"


def _derived_summary(pairs: Iterable[tuple[str, str]]) -> Optional[str]:
    text = " · ".join(f"{label}: {value}" for label, value in pairs)
    if not text:
        return None
    return text if len(text) <= _SUMMARY_MAX else text[: _SUMMARY_MAX - 1] + "…"


def _refresh_page(session: Session, page: WikiPage, now: datetime) -> None:
    claims = active_claims(session, page.id)
    page.facts_json = json.dumps({c.key: c.value for c in claims}, ensure_ascii=False)
    if page.entity_key is not None or not page.summary:
        page.summary = _derived_summary((c.label or humanize_key(c.key), c.value) for c in claims)
    page.updated_at = now
    session.add(page)


def append_log(
    session: Session,
    operation: WikiOperation,
    description: str,
    *,
    document_id: Optional[int] = None,
    record_id: Optional[int] = None,
    page_ids: Sequence[int] = (),
    commit: bool = True,
) -> WikiLogEntry:
    entry = WikiLogEntry(operation=operation, description=description, document_id=document_id,
                         record_id=record_id, page_ids_json=json.dumps(list(page_ids)))
    session.add(entry)
    if commit:
        session.commit()
        session.refresh(entry)
    else:
        session.flush()
    return entry


def apply_claims(
    session: Session,
    claims: Sequence[ClaimInput],
    *,
    document: Optional[Document] = None,
    record: Optional[Record] = None,
    domain: Optional[Domain] = None,
    operation: WikiOperation = WikiOperation.INGEST,
    description: Optional[str] = None,
    links: Sequence[LinkInput] = (),
    retract_missing: bool = False,
) -> IngestReport:
    source_given = document is not None or record is not None
    domain = domain if domain is not None else (
        record.domain if record is not None else (document.domain if document is not None else None)
    )
    now = datetime.utcnow()
    report = IngestReport()
    touched: dict[int, WikiPage] = {}
    changed: set[int] = set()
    asserted: set[tuple[int, str]] = set()

    for claim_input in claims:
        page = _get_or_create_page(session, claim_input.page, domain)
        touched[page.id] = page
        asserted.add((page.id, claim_input.key))
        if claim_input.page.summary and page.entity_key is None and page.summary != claim_input.page.summary:
            page.summary = claim_input.page.summary
            changed.add(page.id)

        active = _active_claim(session, page.id, claim_input.key)
        if active is not None and active.value == claim_input.value and active.note == claim_input.note:
            if source_given:
                _add_source(session, active.id, document, record)
            report.confirmed += 1
            continue

        new = WikiClaim(page_id=page.id, key=claim_input.key, label=claim_input.label, value=claim_input.value,
                        note=claim_input.note)
        session.add(new)
        session.flush()
        if source_given:
            _add_source(session, new.id, document, record)
        report.added += 1
        changed.add(page.id)

        if active is None:
            continue
        is_correction = source_given and _has_source(session, active.id, document, record)
        if claim_input.policy == FactPolicy.LATEST and claim_input.value < active.value and not is_correction:
            new.status = ClaimStatus.SUPERSEDED
            new.superseded_by_claim_id = active.id
            new.superseded_at = now
            session.add(new)
        else:
            active.status = ClaimStatus.SUPERSEDED
            active.superseded_by_claim_id = new.id
            active.superseded_at = now
            session.add(active)
        report.superseded += 1

    for link in links:
        source_page = _get_or_create_page(session, link.from_page, domain)
        target_page = _get_or_create_page(session, link.to_page, domain)
        touched[source_page.id] = source_page
        touched[target_page.id] = target_page
        report.links_added += add_link(session, source_page.id, target_page.id, commit=False)
        if link.bidirectional:
            report.links_added += add_link(session, target_page.id, source_page.id, commit=False)

    if retract_missing and source_given:
        linked_claims = session.exec(
            select(WikiClaim).join(WikiClaimSource, WikiClaimSource.claim_id == WikiClaim.id).where(
                _source_filter(document, record), WikiClaimSource.withdrawn_at.is_(None),
                WikiClaim.status == ClaimStatus.ACTIVE,
            )
        ).all()
        for claim in linked_claims:
            if (claim.page_id, claim.key) in asserted:
                continue
            others = [l for l in _active_links(session, claim.id)
                      if not ((record is not None and l.record_id == record.id)
                              or (record is None and l.document_id == document.id))]
            if others:
                own = session.exec(select(WikiClaimSource).where(
                    WikiClaimSource.claim_id == claim.id, _source_filter(document, record))).one()
                own.withdrawn_at = now
                session.add(own)
            else:
                claim.status = ClaimStatus.SUPERSEDED
                claim.superseded_at = now
                session.add(claim)
                changed.add(claim.page_id)
                touched.setdefault(claim.page_id, session.get(WikiPage, claim.page_id))
            report.retracted += 1

    session.flush()
    for page_id in changed:
        _refresh_page(session, touched[page_id], now)

    report.page_ids = list(touched)
    if description is None:
        source = document.filename if document else (_record_source_label(domain, record) if record else "manual entry")
        titles = ", ".join(p.topic for p in touched.values()) or "no wiki pages"
        description = f"{source} → {titles}"
    entry = append_log(
        session, operation, description,
        document_id=document.id if document else (record.document_id if record else None),
        record_id=record.id if record else None,
        page_ids=report.page_ids, commit=False,
    )
    session.commit()
    report.log_entry_id = entry.id
    return report


def recent_log(session: Session, limit: int = 200) -> list[WikiLogEntry]:
    return list(session.exec(
        select(WikiLogEntry).order_by(WikiLogEntry.occurred_at.desc(), WikiLogEntry.id.desc()).limit(limit)
    ).all())


@dataclass
class IndexEntry:
    page_id: int
    title: str
    summary: Optional[str]
    updated_at: datetime


@dataclass
class IndexSection:
    domain_label: str
    section_label: str
    entries: list[IndexEntry]


def build_wiki_index(session: Session) -> list[IndexSection]:
    from app.domains.registry import implemented_domains

    specs = implemented_domains()
    domain_order = {spec.domain: i for i, spec in enumerate(specs)}
    domain_labels = {spec.domain: spec.label for spec in specs}
    type_labels = {TOPIC_PAGE_TYPE: "Topics", ANSWER_PAGE_TYPE: "Saved answers"}
    for spec in specs:
        for entity_type in spec.wiki.entity_types:
            type_labels[entity_type.page_type] = entity_type.label

    grouped: dict[tuple[Optional[Domain], str], list[IndexEntry]] = {}
    for page in session.exec(select(WikiPage)).all():
        page_type = page.page_type or TOPIC_PAGE_TYPE
        summary = page.summary or _derived_summary(json.loads(page.facts_json or "{}").items())
        grouped.setdefault((page.domain, page_type), []).append(
            IndexEntry(page_id=page.id, title=page.topic, summary=summary, updated_at=page.updated_at)
        )

    def sort_key(key: tuple[Optional[Domain], str]):
        domain, page_type = key
        return (domain_order.get(domain, len(domain_order)), page_type == TOPIC_PAGE_TYPE,
                type_labels.get(page_type, page_type))

    sections = []
    for (domain, page_type), entries in sorted(grouped.items(), key=lambda item: sort_key(item[0])):
        if domain is None:
            domain_label = "General"
        else:
            domain_label = domain_labels.get(domain, domain.value.replace("_", " ").title())
        sections.append(IndexSection(
            domain_label=domain_label,
            section_label=type_labels.get(page_type, page_type.replace("_", " ").title()),
            entries=sorted(entries, key=lambda e: e.title.lower()),
        ))
    return sections