"""Deterministic (no-LLM) wiki health checks. Pure reads. Claim support is
read only through claim_sources() (withdrawn links never count)."""

from __future__ import annotations

import re
from collections import defaultdict

from sqlmodel import Session, select

from app.domains.base import SourceKind
from app.domains.fields import effective_fields, load_fields
from app.domains.registry import implemented_domains
from app.models.record import Record
from app.models.wiki import ANSWER_PAGE_TYPE, ClaimStatus, WikiClaim, WikiLink, WikiLogEntry, WikiOperation, WikiPage
from app.models.wiki_lint import LintFindingKind
from app.services.ask.refs import source_ref
from app.services.ask.tools import fold, is_settled_source, settled_entries
from app.services.wiki_lint.findings import FindingDraft
from app.services.wiki_store import active_claims, claim_sources, links_from, normalize_entity_key

_MIN_TOPIC_CHARS = 4


def _links(session: Session) -> set[tuple[int, int]]:
    return {(l.from_page_id, l.to_page_id) for l in session.exec(select(WikiLink)).all()}


def _active(session: Session) -> list[WikiClaim]:
    return list(session.exec(select(WikiClaim).where(WikiClaim.status == ClaimStatus.ACTIVE)).all())


def find_orphan_pages(session: Session) -> list[FindingDraft]:
    linked = {p for pair in _links(session) for p in pair}
    return [FindingDraft(LintFindingKind.ORPHAN_PAGE, f"“{p.topic}” isn't linked to or from any other wiki page.",
                         domain=p.domain, wiki_page_ids=(p.id,),
                         suggested_action="Link it from a related page, or dismiss if it stands on its own.")
            for p in session.exec(select(WikiPage).order_by(WikiPage.id)).all() if p.id not in linked]


def find_unsourced_claims(session: Session) -> list[FindingDraft]:
    pages = {p.id: p for p in session.exec(select(WikiPage)).all()}
    claims = [c for c in _active(session) if pages[c.page_id].page_type != ANSWER_PAGE_TYPE]
    supported = claim_sources(session, [c.id for c in claims])   # active (non-withdrawn) links only
    by_page: dict[int, list[WikiClaim]] = defaultdict(list)
    for c in claims:
        if not supported.get(c.id):
            by_page[c.page_id].append(c)
    return [FindingDraft(LintFindingKind.UNSOURCED_CLAIM,
                         f"“{pages[pid].topic}” has {len(cs)} fact(s) with no current source "
                         f"(never had one, or its source was re-filed elsewhere): {', '.join(c.label or c.key for c in cs)}.",
                         domain=pages[pid].domain, wiki_page_ids=(pid,), claim_ids=tuple(c.id for c in cs),
                         suggested_action="Check whether these facts are still true; dismiss if they were entered by hand.")
            for pid, cs in sorted(by_page.items())]


def find_missing_links(session: Session) -> list[FindingDraft]:
    pages = list(session.exec(select(WikiPage).order_by(WikiPage.id)).all())
    links = _links(session)
    text_by_page: dict[int, str] = defaultdict(str)
    for c in _active(session):
        text_by_page[c.page_id] += " " + fold(c.value)
    drafts = []
    for source in pages:
        haystack = text_by_page.get(source.id, "")
        for target in pages:
            topic = fold(target.topic)
            if target.id == source.id or len(topic) < _MIN_TOPIC_CHARS or (source.id, target.id) in links:
                continue
            if re.search(rf"\b{re.escape(topic)}\b", haystack):
                drafts.append(FindingDraft(
                    LintFindingKind.MISSING_LINK, f"“{source.topic}” mentions “{target.topic}” but doesn't link to it.",
                    domain=source.domain, wiki_page_ids=(source.id, target.id), suggested_action="Add link"))
    return drafts


def find_stale_links(session: Session) -> list[FindingDraft]:
    drafts = []
    for spec in implemented_domains():
        for entity_type in spec.wiki.entity_types:
            for link in entity_type.links:
                for page in session.exec(select(WikiPage).where(WikiPage.page_type == entity_type.page_type)).all():
                    claims = active_claims(session, page.id)
                    sources = {source_ref(s): s for ss in claim_sources(session, [c.id for c in claims]).values()
                               for s in ss if is_settled_source(s)}
                    candidates = []
                    for source in sources.values():
                        value = effective_fields(spec, source.category, load_fields(source)).get(link.field)
                        if value and (link.categories is None or source.category in link.categories):
                            candidates.append((source.created_at, source.id, source, value))
                    if not candidates:
                        continue
                    _, _, newest, current_value = max(candidates, key=lambda c: (c[0], c[1]))
                    current_key = normalize_entity_key(current_value)
                    is_record = isinstance(newest, Record)
                    for target in links_from(session, page.id):
                        if target.page_type == link.target_page_type and target.entity_key != current_key:
                            drafts.append(FindingDraft(
                                LintFindingKind.STALE_LINK,
                                f"“{page.topic}” is still linked to “{target.topic}”, but its latest source "
                                f"({source_ref(newest)}) says “{current_value}”.",
                                domain=spec.domain, wiki_page_ids=(page.id, target.id),
                                document_ids=() if is_record else (newest.id,),
                                record_ids=(newest.id,) if is_record else (),
                                suggested_action="Links are kept as history. Check the latest source is right, "
                                                 "then dismiss (or correct its details)."))
    return drafts


def find_uningested_sources(session: Session) -> list[FindingDraft]:
    """Settled documents and records with no INGEST log entry (A guarantees one for each)."""
    logs = session.exec(select(WikiLogEntry).where(WikiLogEntry.operation == WikiOperation.INGEST)).all()
    doc_ids = {e.document_id for e in logs if e.document_id}
    rec_ids = {e.record_id for e in logs if e.record_id}
    drafts = []
    for spec in implemented_domains():
        missing_docs, missing_recs = [], []
        for entry in settled_entries(session, spec.domain):
            if entry.kind == SourceKind.RECORD:
                attached = entry.document.id if entry.document is not None else None
                if entry.record.id not in rec_ids and attached not in doc_ids:
                    missing_recs.append(entry.record.id)
            elif entry.document.id not in doc_ids:
                missing_docs.append(entry.document.id)
        if missing_docs or missing_recs:
            drafts.append(FindingDraft(
                LintFindingKind.UNINGESTED_SOURCES,
                f"{len(missing_docs) + len(missing_recs)} {spec.label} source(s) "
                f"({len(missing_docs)} document(s), {len(missing_recs)} record(s)) have never been read into the wiki.",
                domain=spec.domain, document_ids=tuple(sorted(missing_docs)), record_ids=tuple(sorted(missing_recs)),
                suggested_action="Re-run wiki ingest for these sources."))
    return drafts


def find_stale_saved_answers(session: Session) -> list[FindingDraft]:
    links = _links(session)
    claims = list(session.exec(select(WikiClaim)).all())
    drafts = []
    for ans in session.exec(select(WikiPage).where(WikiPage.page_type == ANSWER_PAGE_TYPE)).all():
        saved_at = min((c.created_at for c in claims if c.page_id == ans.id), default=None)
        if saved_at is None:
            continue
        targets = {t for f, t in links if f == ans.id}
        changed = sorted({c.page_id for c in claims if c.page_id in targets and (
            c.created_at > saved_at or (c.superseded_at and c.superseded_at > saved_at))})
        if changed:
            names = ", ".join(f"“{session.get(WikiPage, i).topic}”" for i in changed)
            drafts.append(FindingDraft(
                LintFindingKind.STALE_SAVED_ANSWER,
                f"Saved answer “{ans.topic}” may be out of date: {names} changed since it was saved.",
                domain=ans.domain, wiki_page_ids=(ans.id, *changed),
                suggested_action="Ask the question again and re-save it, or dismiss if still correct."))
    return drafts


def run_deterministic_checks(session: Session) -> list[FindingDraft]:
    return (find_orphan_pages(session) + find_unsourced_claims(session) + find_missing_links(session)
            + find_stale_links(session) + find_uningested_sources(session) + find_stale_saved_answers(session))
