"""Wiki pages and the subjects registry (PLAN step 6a, 6b): which subject a page is about, one spelling per fact key,
and a provider fact written as its subject's own name.

- An entity page (a person, a policy, a thing) is about the subject its kind declares (`EntityTypeSpec.subject_kind`).
- A topic page titled "Telecom — Amigo" is about the provider after the dash; one titled "Pedro: check-ups" is about
  a person the registry already holds (a topic title never creates a person).
- A provider written another way ("Amigo (Vodafone)") leads to its subject through the subject's aliases, so a second
  page is not made for it and its "provider" fact keeps one value.
Pure lookups: nothing here joins two subjects (that is a person's yes, or a plan step's alias)."""

from __future__ import annotations

import re
from typing import Optional

from sqlmodel import Session, select

from app.models.subject import Subject, SubjectKind
from app.models.wiki import TOPIC_PAGE_TYPE, WikiPage
from app.services import subjects
from app.services.names import name_key

TITLE_SEP = " — "
PROVIDER_KEYS = frozenset({"provider"})          # facts whose value names the page's provider


def fold_key(key: str) -> str:
    """One spelling per fact key: "Provider" and "provider", "Statement period" and "statement_period"."""
    folded = re.sub(r"[^a-z0-9]+", "_", name_key(str(key))).strip("_")
    return folded or str(key)


def _entity_kinds() -> dict[str, SubjectKind]:
    from app.categories.registry import implemented_categories
    return {e.page_type: SubjectKind(e.subject_kind) for spec in implemented_categories()
            for e in spec.wiki.entity_types if e.subject_kind}


def _spellings(name: str) -> list[str]:
    """The name, then its parts around brackets: "Amigo (Vodafone Portugal)" → itself, "Amigo", "Vodafone Portugal"."""
    out = [name]
    m = re.match(r"^\s*(.*?)\s*\((.*?)\)\s*$", name)
    if m:
        out += [p for p in m.groups() if p.strip()]
    return out


def find_counterparty(session: Session, name: Optional[str]) -> Optional[Subject]:
    for spelling in _spellings(name or ""):
        found = subjects.find(session, SubjectKind.COUNTERPARTY, spelling)
        if found is not None:
            return found
    return None


def _provider_in_title(title: str) -> Optional[str]:
    if TITLE_SEP not in title:
        return None
    name = title.split(TITLE_SEP, 1)[1].strip()
    name = re.sub(r"\s+provider$", "", name, flags=re.I).strip()       # "Insurance — MetLife provider"
    return name or None


def subject_for(session: Session, page_type: str, title: str) -> Optional[Subject]:
    """The subject a new page with this type and title is about, registering a person, policy, thing or provider
    when it is new. None when the title names nobody the registry can hold."""
    kind = _entity_kinds().get(page_type)
    if kind is not None:
        return subjects.register(session, kind, title)
    if page_type != TOPIC_PAGE_TYPE:
        return None
    provider = _provider_in_title(title)
    if provider is not None:
        return find_counterparty(session, provider) or subjects.register(session, SubjectKind.COUNTERPARTY, provider)
    if ":" in title:
        return _person_named(session, title.split(":", 1)[0])
    return None


def _person_named(session: Session, name: str) -> Optional[Subject]:
    """"Pedro: check-ups": the person with exactly that name, else the only person whose first name it is."""
    exact = subjects.find(session, SubjectKind.PERSON, name)
    if exact is not None:
        return exact
    words = name_key(name).split()
    if len(words) != 1:
        return None
    same = [s for s in session.exec(select(Subject).where(Subject.kind == SubjectKind.PERSON,
                                                          Subject.merged_into_id.is_(None))).all()
            if name_key(s.name).split()[:1] == words]
    return same[0] if len(same) == 1 else None


def root_id(session: Session, subject_id: Optional[int]) -> Optional[int]:
    if subject_id is None:
        return None
    subject = session.get(Subject, subject_id)
    return subjects.resolve(session, subject).id if subject is not None else None


def topic_page_about(session: Session, subject: Subject, category) -> Optional[WikiPage]:
    """An existing topic page of this category about this provider (any spelling): reused, never a second page."""
    if subject.kind != SubjectKind.COUNTERPARTY:
        return None
    for page in session.exec(select(WikiPage).where(WikiPage.page_type == TOPIC_PAGE_TYPE,
                                                    WikiPage.subject_id.is_not(None),
                                                    WikiPage.category == category).order_by(WikiPage.id)).all():
        if root_id(session, page.subject_id) == subject.id:
            return page
    return None


def canonical_value(session: Session, page: WikiPage, key: str, value: str) -> str:
    """A provider fact naming the page's own provider, in any spelling, is written as that provider's name."""
    if key not in PROVIDER_KEYS or page.subject_id is None:
        return value
    own = root_id(session, page.subject_id)
    found = find_counterparty(session, value)
    return session.get(Subject, own).name if found is not None and found.id == own else value


def pages_about(session: Session, subject_id: int) -> list[WikiPage]:
    """Every page about this subject or one of its aliases, entity pages first."""
    root = root_id(session, subject_id)
    if root is None:
        return []
    pages = [p for p in session.exec(select(WikiPage).where(WikiPage.subject_id.is_not(None))).all()
             if root_id(session, p.subject_id) == root]
    return sorted(pages, key=lambda p: (p.page_type == TOPIC_PAGE_TYPE, p.topic.lower()))


__all__ = ["fold_key", "subject_for", "find_counterparty", "topic_page_about", "canonical_value", "pages_about",
           "root_id", "PROVIDER_KEYS", "TITLE_SEP"]


# --- one-time settling of the pages already on file (scripts/settle_wiki_subjects.py) ---------------------------

# Providers written several ways on the bills (PLAN step 6b; Claude, 2026-11-20; Pedro may change which name is kept).
PROVIDER_ALIASES: dict[str, tuple[str, ...]] = {
    "Vodafone Portugal": ("Amigo", "Amigo (Vodafone)", "Amigo (Vodafone Portugal)", "Vodafone"),
}


def _settle_keys(session: Session, page: WikiPage, now) -> tuple[int, int]:
    """Fold the page's keys; where two current facts now share a key, the one true latest stays current (as
    `apply_claims` decides), the others become its history. Returns (renamed, superseded)."""
    from app.models.wiki import ClaimStatus, WikiClaim
    renamed = superseded = 0
    claims = session.exec(select(WikiClaim).where(WikiClaim.page_id == page.id)).all()
    for c in claims:
        folded = fold_key(c.key)
        if folded != c.key:
            c.label = c.label or c.key
            c.key = folded
            session.add(c)
            renamed += 1
    by_key: dict[str, list] = {}
    for c in claims:
        if c.status == ClaimStatus.ACTIVE:
            by_key.setdefault(c.key, []).append(c)
    for current in by_key.values():
        if len(current) < 2:
            continue
        current.sort(key=lambda c: (c.valid_from is not None, c.valid_from or now.date(), c.created_at), reverse=True)
        keep = current[0]
        for c in current[1:]:
            c.status, c.superseded_at, c.superseded_by_claim_id = ClaimStatus.SUPERSEDED, now, keep.id
            session.add(c)
            superseded += 1
    return renamed, superseded


def _move_page(session: Session, drop: WikiPage, keep: WikiPage) -> None:
    """Everything that points at `drop` points at `keep`; then `drop` goes (its claims, with their papers, move)."""
    from app.models.ask import AskTurn
    from app.models.wiki import WikiChange, WikiClaim, WikiLink
    for c in session.exec(select(WikiClaim).where(WikiClaim.page_id == drop.id)).all():
        c.page_id = keep.id
        session.add(c)
    for ch in session.exec(select(WikiChange).where(WikiChange.wiki_page_id == drop.id)).all():
        ch.wiki_page_id = keep.id
        session.add(ch)
    for t in session.exec(select(AskTurn).where(AskTurn.saved_wiki_page_id == drop.id)).all():
        t.saved_wiki_page_id = keep.id
        session.add(t)
    pairs = {(l.from_page_id, l.to_page_id) for l in session.exec(select(WikiLink)).all()}
    for link in session.exec(select(WikiLink).where((WikiLink.from_page_id == drop.id) | (WikiLink.to_page_id == drop.id))).all():
        a = keep.id if link.from_page_id == drop.id else link.from_page_id
        b = keep.id if link.to_page_id == drop.id else link.to_page_id
        if a == b or (a, b) in pairs:
            session.delete(link)
        else:
            link.from_page_id, link.to_page_id = a, b
            pairs.add((a, b))
            session.add(link)
    session.flush()
    session.delete(drop)
    session.flush()


def settle(session: Session, aliases: dict[str, tuple[str, ...]] = PROVIDER_ALIASES) -> dict:
    """Give every page its subject, add the providers' aliases, join the topic pages about one provider, fold the
    keys, and write each provider fact as its provider's name. Idempotent. Returns what it did, for the record."""
    from datetime import datetime
    from app.models.wiki import ClaimStatus, WikiClaim
    now = datetime.utcnow()
    out = {"subjects_given": 0, "aliases": 0, "pages_joined": [], "keys_renamed": 0, "facts_superseded": 0,
           "provider_values": 0}
    for name, spellings in aliases.items():
        subject = subjects.register(session, SubjectKind.COUNTERPARTY, name)
        for spelling in spellings:
            found = subjects.find(session, SubjectKind.COUNTERPARTY, spelling)
            if found is None or found.id != subject.id:
                subjects.add_alias(session, subject, spelling)
                out["aliases"] += 1
    for page in session.exec(select(WikiPage).where(WikiPage.subject_id.is_(None)).order_by(WikiPage.id)).all():
        subject = subject_for(session, page.page_type, page.topic)
        if subject is not None:
            page.subject_id = subject.id
            session.add(page)
            out["subjects_given"] += 1
    session.flush()
    groups: dict[tuple, list[WikiPage]] = {}
    for page in session.exec(select(WikiPage).where(WikiPage.page_type == TOPIC_PAGE_TYPE,
                                                    WikiPage.subject_id.is_not(None)).order_by(WikiPage.id)).all():
        subject = session.get(Subject, root_id(session, page.subject_id))
        if subject.kind == SubjectKind.COUNTERPARTY:
            groups.setdefault((page.category, subject.id), []).append(page)
    for pages in groups.values():
        keep = pages[0]
        for drop in pages[1:]:
            out["pages_joined"].append(f"{drop.topic} → {keep.topic}")
            _move_page(session, drop, keep)
    for page in session.exec(select(WikiPage)).all():
        renamed, superseded = _settle_keys(session, page, now)
        out["keys_renamed"] += renamed
        out["facts_superseded"] += superseded
        for c in session.exec(select(WikiClaim).where(WikiClaim.page_id == page.id,
                                                      WikiClaim.status == ClaimStatus.ACTIVE)).all():
            value = canonical_value(session, page, c.key, c.value)
            if value != c.value:
                c.value = value
                session.add(c)
                out["provider_values"] += 1
    session.flush()
    return out
