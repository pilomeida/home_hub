"""Knowledge-layer storage: HOW the wiki is stored. Pure database work, no
LLM (app.services.wiki_engine decides WHAT to record).

Pages are made of claims; every claim links to the Document(s) asserting
it; a changed claim supersedes the old one (never deleted); every call to
apply_claims appends one entry to the wiki log; the index is generated.
Plan C's Query (filing answers back) and Lint also go through here."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Iterable, Optional, Sequence

from sqlmodel import Session, select

from app.categories.base import FactPolicy
from app.categories.registry import get_spec, is_implemented
from app.models.document import Document
from app.models.category import Category
from app.models.record import Record
from app.models.wiki import (
    ANSWER_PAGE_TYPE, TOPIC_PAGE_TYPE, ClaimStatus, WikiClaim, WikiClaimSource, WikiLink, WikiLogEntry,
    WikiOperation, WikiPage,
)
from app.services.presentation import claim_label

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
    # When the fact was true, if the paper says so itself (a tariff "from 2025-01-01"); otherwise apply_claims dates
    # it from the paper (`as_of`). Graphiti's valid time, see WikiClaim.
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None


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


def _get_or_create_page(session: Session, ref: PageRef, category: Optional[Category]) -> WikiPage:
    from app.services import wiki_subjects
    page = find_page(session, ref)
    if page is not None:
        return page
    subject = wiki_subjects.subject_for(session, ref.page_type, ref.title)
    if subject is not None:                   # "Telecom — Amigo (Vodafone)": the provider's page, not a second one
        same = wiki_subjects.topic_page_about(session, subject, category)
        if same is not None:
            return same
    taken = set(session.exec(select(WikiPage.topic).where(WikiPage.topic.in_(_candidate_titles(ref)))).all())
    title = next((t for t in _candidate_titles(ref) if t not in taken), None)
    if title is None:
        raise ValueError(f"Wiki title collision for {ref.title!r} ({ref.page_type})")
    page = WikiPage(topic=title, page_type=ref.page_type, entity_key=ref.entity_key,
                    summary=ref.summary, category=category, subject_id=subject.id if subject is not None else None)
    session.add(page)
    session.flush()
    return page


def ensure_page(session: Session, ref: PageRef, category: Optional[Category]) -> WikiPage:
    return _get_or_create_page(session, ref, category)


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


def _record_source_label(category: Optional[Category], record: Record) -> str:
    """A hand-entered Record's default log-description source, e.g.
    "Maintenance log (entered by hand) #1" -- the registry's category
    label, not the raw internal category key."""
    kind_label = record.paper_kind
    if category is not None and is_implemented(category):
        kind_label = get_spec(category).kind_label(record.paper_kind) or record.paper_kind
    return f"{kind_label} (entered by hand) #{record.id}"


def _derived_summary(pairs: Iterable[tuple[str, str]]) -> Optional[str]:
    text = " · ".join(f"{label}: {value}" for label, value in pairs)
    if not text:
        return None
    return text if len(text) <= _SUMMARY_MAX else text[: _SUMMARY_MAX - 1] + "…"


def _refresh_page(session: Session, page: WikiPage, now: datetime) -> None:
    claims = active_claims(session, page.id)
    page.facts_json = json.dumps({c.key: c.value for c in claims}, ensure_ascii=False)
    if page.entity_key is not None or not page.summary:
        page.summary = _derived_summary((claim_label(c.label, c.key), c.value) for c in claims)
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
    category: Optional[Category] = None,
    operation: WikiOperation = WikiOperation.INGEST,
    description: Optional[str] = None,
    links: Sequence[LinkInput] = (),
    retract_missing: bool = False,
    as_of: Optional[date] = None,
) -> IngestReport:
    """Write claims, never deleting one. Two clocks (Graphiti's bi-temporal model): created_at/superseded_at say
    when the Hub learned or replaced a fact; valid_from/valid_to say when it was true. `as_of` is the date of the
    paper (its kind's `dated_by`), the start of every claim that names none of its own. A paper older than the fact
    on file adds history, it does not overwrite the present: its claim is recorded as already ended where the
    current one begins."""
    source_given = document is not None or record is not None
    category = category if category is not None else (
        record.category if record is not None else (document.category if document is not None else None)
    )
    now = datetime.utcnow()
    if as_of is None and source_given:
        as_of = _paper_date(session, record if record is not None else document)
    report = IngestReport()
    touched: dict[int, WikiPage] = {}
    changed: set[int] = set()
    asserted: set[tuple[int, str]] = set()

    from app.services.wiki_subjects import canonical_value, fold_key
    for claim_input in claims:
        page = _get_or_create_page(session, claim_input.page, category)
        key = fold_key(claim_input.key)               # one spelling per key: "Provider" is "provider" (step 6b)
        claim_input = replace(claim_input, key=key, label=claim_input.label or claim_input.key,
                              value=canonical_value(session, page, key, claim_input.value))
        touched[page.id] = page
        asserted.add((page.id, claim_input.key))
        if (claim_input.page.summary and page.entity_key is None and page.summary != claim_input.page.summary
                and page.topic in _candidate_titles(claim_input.page)):    # another spelling's page keeps its own
            page.summary = claim_input.page.summary
            changed.add(page.id)

        valid_from = claim_input.valid_from or as_of
        active = _active_claim(session, page.id, claim_input.key)
        if active is not None and active.value == claim_input.value and active.note == claim_input.note:
            if source_given:
                _add_source(session, active.id, document, record)
            if valid_from is not None and (active.valid_from is None or valid_from < active.valid_from):
                active.valid_from = valid_from           # an earlier paper saying the same: true since then
                session.add(active)
            report.confirmed += 1
            continue

        new = WikiClaim(page_id=page.id, key=claim_input.key, label=claim_input.label, value=claim_input.value,
                        note=claim_input.note, valid_from=valid_from, valid_to=claim_input.valid_to)
        session.add(new)
        session.flush()
        if source_given:
            _add_source(session, new.id, document, record)
        report.added += 1
        changed.add(page.id)

        if active is None:
            continue
        is_correction = source_given and _has_source(session, active.id, document, record)
        confirmed_on = [] if is_correction else _asserted_dates(session, active)
        # The fact on file stays current unless this paper is newer than its LATEST confirmation; an undated paper
        # never replaces a dated fact (where neither is dated, the newest filed wins, as before).
        stays = not is_correction and (
            (bool(confirmed_on) and (new.valid_from is None or new.valid_from < max(confirmed_on)))
            or (claim_input.policy == FactPolicy.LATEST and claim_input.value < active.value)
        )
        if stays:
            new.status = ClaimStatus.SUPERSEDED          # history: true until the fact on file was next confirmed
            new.superseded_by_claim_id = active.id
            new.superseded_at = now
            later = [d for d in confirmed_on if new.valid_from is not None and d > new.valid_from]
            if later and (new.valid_to is None or new.valid_to > min(later)):
                new.valid_to = min(later)
            session.add(new)
        else:
            active.status = ClaimStatus.SUPERSEDED
            active.superseded_by_claim_id = new.id
            active.superseded_at = now
            if is_correction:
                active.was_correction = True             # the same paper corrected it: never true as stated
            elif active.valid_to is None and new.valid_from is not None:
                active.valid_to = new.valid_from         # true until the new fact took over
            session.add(active)
        report.superseded += 1

    for link in links:
        source_page = _get_or_create_page(session, link.from_page, category)
        target_page = _get_or_create_page(session, link.to_page, category)
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
        source = document.filename if document else (_record_source_label(category, record) if record else "manual entry")
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
    about: Optional[str] = None      # the subject the page is about (step 6a)


@dataclass
class IndexSection:
    category_label: str
    section_label: str
    entries: list[IndexEntry]


def visible_summary(session: Session, page: WikiPage, view) -> Optional[str]:
    """A page's summary as Ask / the wiki check may see it. The stored summary (and facts_json) can
    repeat claims that are hidden from them, so a page with any hidden claim gets one rebuilt from
    its visible active claims only. `view` is app.privacy.reach.WikiView (duck-typed: no import)."""
    if view is None or page.id not in view.trimmed_pages:
        return page.summary or _derived_summary(json.loads(page.facts_json or "{}").items())
    return _derived_summary((claim_label(c.label, c.key), c.value)
                            for c in active_claims(session, page.id) if view.claim_visible(c.id))


def _about(session: Session, page: WikiPage) -> Optional[str]:
    from app.services.wiki_subjects import root_id
    from app.models.subject import Subject
    root = root_id(session, page.subject_id)
    return session.get(Subject, root).name if root is not None else None


def build_wiki_index(session: Session, view=None) -> list[IndexSection]:
    """`view` (app.privacy.reach.WikiView) hides the pages and claims Ask may not see; None = all."""
    from app.categories.registry import implemented_categories

    specs = implemented_categories()
    category_order = {spec.category: i for i, spec in enumerate(specs)}
    category_labels = {spec.category: spec.label for spec in specs}
    type_labels = {TOPIC_PAGE_TYPE: "Topics", ANSWER_PAGE_TYPE: "Saved answers"}
    for spec in specs:
        for entity_type in spec.wiki.entity_types:
            type_labels[entity_type.page_type] = entity_type.label

    grouped: dict[tuple[Optional[Category], str], list[IndexEntry]] = {}
    for page in session.exec(select(WikiPage)).all():
        if view is not None and not view.page_visible(page.id):
            continue
        page_type = page.page_type or TOPIC_PAGE_TYPE
        summary = visible_summary(session, page, view)
        grouped.setdefault((page.category, page_type), []).append(
            IndexEntry(page_id=page.id, title=page.topic, summary=summary, updated_at=page.updated_at,
                       about=_about(session, page))
        )

    def sort_key(key: tuple[Optional[Category], str]):
        category, page_type = key
        return (category_order.get(category, len(category_order)), page_type == TOPIC_PAGE_TYPE,
                type_labels.get(page_type, page_type))

    sections = []
    for (category, page_type), entries in sorted(grouped.items(), key=lambda item: sort_key(item[0])):
        if category is None:
            category_label = "General"
        else:
            category_label = category_labels.get(category, category.value.replace("_", " ").title())
        sections.append(IndexSection(
            category_label=category_label,
            section_label=type_labels.get(page_type, page_type.replace("_", " ").title()),
            entries=sorted(entries, key=lambda e: e.title.lower()),
        ))
    return sections

def _paper_date(session: Session, source: Optional[Document | Record]) -> Optional[date]:
    """The date a paper speaks for, by its kind's `dated_by` (else its category's); None when unknown."""
    from app.categories.registry import get_spec   # lazy: the registry imports every spec
    if source is None or source.category is None:
        return None
    return get_spec(source.category).paper_date(session, source)


def _asserted_dates(session: Session, claim: WikiClaim) -> list[date]:
    """Every date a paper asserted this claim (its sources), plus its own start."""
    dates = [claim.valid_from] if claim.valid_from is not None else []
    for link in session.exec(select(WikiClaimSource).where(WikiClaimSource.claim_id == claim.id,
                                                           WikiClaimSource.withdrawn_at.is_(None))).all():
        when = _paper_date(session, session.get(Document, link.document_id) if link.document_id
                           else session.get(Record, link.record_id))
        if when is not None:
            dates.append(when)
    return dates


@dataclass
class DatingReport:
    dated: int = 0                 # facts given a start from their earliest paper
    closed: int = 0                # replaced facts given an end where their successor begins
    out_of_order: list[int] = field(default_factory=list)   # replaced by a fact from an OLDER paper: to look at


def date_existing_facts(session: Session) -> DatingReport:
    """Date the facts filed before the wiki knew when facts were true (migration a3c7e1f9b250): each fact starts
    at the earliest date of the papers that say it, and a replaced fact ends where its successor starts. A fact
    replaced by one from an older paper is listed, not changed: which is current is a person's call. The caller
    commits; safe to run again (only empty dates are filled)."""
    report = DatingReport()
    claims = list(session.exec(select(WikiClaim)).all())
    for claim in claims:
        if claim.valid_from is not None:
            continue
        dates = []
        for link in session.exec(select(WikiClaimSource).where(WikiClaimSource.claim_id == claim.id)).all():
            when = _paper_date(session, session.get(Document, link.document_id) if link.document_id
                               else session.get(Record, link.record_id))
            if when is not None:
                dates.append(when)
        if dates:
            claim.valid_from = min(dates)
            session.add(claim)
            report.dated += 1
    by_id = {c.id: c for c in claims}
    for claim in claims:
        successor = by_id.get(claim.superseded_by_claim_id) if claim.superseded_by_claim_id else None
        if successor is None or claim.was_correction or claim.valid_to is not None or successor.valid_from is None:
            continue
        if claim.valid_from is not None and successor.valid_from < claim.valid_from:
            report.out_of_order.append(claim.id)
            continue
        claim.valid_to = successor.valid_from
        session.add(claim)
        report.closed += 1
    session.flush()
    return report


@dataclass
class Resettled:
    page_id: int
    topic: str
    key: str
    was: str                       # the value shown before
    now: str                       # the value shown after


def resettle_facts(session: Session) -> list[Resettled]:
    """Replay the rule `apply_claims` follows today over every fact on file, for facts filed before it: per page and
    key, the current value is the one a paper asserted LATEST (a dated paper beats an undated one; corrections
    never count); every other value is history, ending where another value was next asserted. Keys with no dated
    paper are left as filed (the newest filed wins). A fact taken back by its paper (retracted: replaced by nothing)
    is never revived, and a key showing nothing stays empty. Returns the keys whose shown value changed; the caller
    commits."""
    from collections import defaultdict
    now_ts = datetime.utcnow()
    groups: dict[tuple[int, str], list[WikiClaim]] = defaultdict(list)
    for claim in session.exec(select(WikiClaim).where(WikiClaim.was_correction.is_(False)).order_by(WikiClaim.id)):
        retracted = claim.status == ClaimStatus.SUPERSEDED and claim.superseded_by_claim_id is None
        if not retracted:          # a fact its paper took back (re-read, re-filed, deleted) is never revived
            groups[(claim.page_id, claim.key)].append(claim)
    changed: list[Resettled] = []
    pages: dict[int, WikiPage] = {}
    for (page_id, key), claims in groups.items():
        dates = {c.id: _asserted_dates(session, c) for c in claims}
        dated = [c for c in claims if dates[c.id]]
        if not dated:
            continue
        current = max(dated, key=lambda c: (max(dates[c.id]), c.id))
        before = next((c for c in claims if c.status == ClaimStatus.ACTIVE), None)
        if before is None:
            continue               # nothing is shown for this key: the replay never brings a value back
        for claim in claims:
            if claim.id == current.id:
                if claim.status != ClaimStatus.ACTIVE:
                    claim.status, claim.superseded_by_claim_id, claim.superseded_at = ClaimStatus.ACTIVE, None, None
                    claim.valid_to = None
                    session.add(claim)
                continue
            if claim.status == ClaimStatus.ACTIVE or claim.superseded_by_claim_id is None:
                claim.status, claim.superseded_by_claim_id = ClaimStatus.SUPERSEDED, current.id
                claim.superseded_at = claim.superseded_at or now_ts
                session.add(claim)
            if dates[claim.id] and claim.valid_to is None:
                last = max(dates[claim.id])
                later = [d for c in claims if c.id != claim.id for d in dates[c.id] if d > last]
                if later:
                    claim.valid_to = min(later)
                    session.add(claim)
        if before.id != current.id:
            page = pages.setdefault(page_id, session.get(WikiPage, page_id))
            if before.value != current.value:
                changed.append(Resettled(page_id, page.topic, key, before.value, current.value))
    session.flush()
    for page in pages.values():
        _refresh_page(session, page, now_ts)
    session.flush()
    return changed
