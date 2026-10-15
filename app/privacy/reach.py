"""What Ask and the weekly wiki check may read once levels are binding (decision D16).

Ask and the wiki check read documents of EVERY type, so no single type's level can govern their
AI calls. Instead, while levels are binding the household has a "reachable set" of types:

  EXCLUDED  a type whose level is High (no route exists), or has no level yet, or whose
            "hide personal details" switch is on (hiding cannot work when the AI also looks
            things up or reads files);
  INCLUDED  every other type.

The AI call level for Ask and the wiki check is the STRICTEST level among the included types
(Low < Mid; if nothing is included, Mid is the floor). Excluded content must never reach the AI:
every place that lists or reads documents, records, wiki claims, wiki pages, transactions or to-dos
for Ask / the wiki check asks the Reach here.

- Levels not binding -> `Reach.binding` is False and every predicate says yes: nothing changes.
- A wiki claim is hidden when ANY of its current sources (documents or records) is excluded. A page
  whose claims are all hidden is hidden. A page with only some claims hidden stays, but its stored
  summary (which may repeat the hidden claims) is replaced by one built from the visible claims.
- Claims and pages with no document source (saved Ask answers) are included.
- A record follows its own type; a record's attached file must also be of an included type.

The Reach is a snapshot taken when a run starts (`reachable_scope` in app/privacy/scope.py) so the
level of the call and the filter always agree, even if someone edits a level mid-run."""

from __future__ import annotations

import contextvars
import json
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Iterable, Optional

from sqlmodel import Session, select

from app.models.document import Document
from app.models.privacy import DocumentTypeSetting
from app.models.record import Record
from app.models.transaction import Transaction
from app.models.wiki import WikiClaim, WikiClaimSource
from app.privacy import settings as ps
from app.privacy.tiers import Tier, TierBlocked, parse_tier
from app.problems import Problem

if TYPE_CHECKING:
    from app.domains.entries import SourceEntry
    from app.models.todo import Todo

HIGH, UNSET, HIDES = "high", "unset", "hides"
_ORDER = {Tier.LOW: 0, Tier.MID: 1}
_REASON_ORDER = (HIGH, UNSET, HIDES)


def type_key(domain: object, category: Optional[str]) -> str:
    return f"{ps._s(domain)}/{category}"


@dataclass(frozen=True)
class TypeAccess:
    domain: str
    category: str
    label: str
    included: bool
    reason: Optional[str]            # HIGH | UNSET | HIDES when excluded
    tier: Optional[Tier]             # the saved level (None = not set)


# --- what was left out, in plain words ----------------------------------------------------------

@dataclass(frozen=True)
class LeftOutType:
    label: str
    reason: str
    documents: int = 0
    records: int = 0


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _names(labels: list[str]) -> str:
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


@dataclass(frozen=True)
class LeftOut:
    """Stored with an Ask answer / a wiki check run. `excluded` lists EVERY excluded type, with or
    without content: it is the snapshot used to decide whether an earlier answer may be replayed."""
    binding: bool = False
    excluded: tuple[str, ...] = ()
    types: tuple[LeftOutType, ...] = ()
    history_dropped: int = 0

    @property
    def documents(self) -> int:
        return sum(t.documents for t in self.types)

    @property
    def records(self) -> int:
        return sum(t.records for t in self.types)

    @property
    def count(self) -> int:
        return self.documents + self.records

    def to_json(self) -> str:
        return json.dumps({"binding": self.binding, "excluded": list(self.excluded),
                           "history_dropped": self.history_dropped,
                           "types": [vars(t) for t in self.types]})

    @staticmethod
    def from_json(raw: Optional[str]) -> "LeftOut":
        if not raw:
            return LeftOut()
        try:
            data = json.loads(raw)
            return LeftOut(bool(data.get("binding")), tuple(data.get("excluded", ())),
                           tuple(LeftOutType(**t) for t in data.get("types", ())), int(data.get("history_dropped", 0)))
        except (ValueError, TypeError):
            return LeftOut()

    def problem(self, subject: str = "answer") -> Optional[Problem]:
        """The three-part note: what wasn't available, why, what to do. subject: "answer" | "check"."""
        if not self.binding or self.count == 0:
            return None
        n_docs, n_recs = self.documents, self.records
        if n_recs:
            what = f"{_plural(n_docs + n_recs, 'document or record', 'documents and records')} " \
                   f"{'wasn' if n_docs + n_recs == 1 else 'weren'}'t available to this {subject}."
        else:
            what = f"{_plural(n_docs, 'document', 'documents')} {'wasn' if n_docs == 1 else 'weren'}'t available to this {subject}."
        parts = []
        for reason in _REASON_ORDER:
            labels = [t.label for t in self.types if t.reason == reason]
            if not labels:
                continue
            many = len(labels) > 1
            if reason == HIGH:
                parts.append(f"{_names(labels)} {'are' if many else 'is'} set to High, and no AI service is approved for that level")
            elif reason == UNSET:
                parts.append(f"{_names(labels)} {'have' if many else 'has'} no level yet")
            else:
                parts.append(f"{_names(labels)} {'hide' if many else 'hides'} personal details, which can't be done "
                             f"while the AI also looks things up or reads files")
        why = "; ".join(parts) + ". Nothing from them was sent to the AI."
        again = "ask again" if subject == "answer" else "run the check again"
        return Problem(what, why, f"Change that on the Privacy page, then {again}.", link="/privacy")

    def history_problem(self) -> Optional[Problem]:
        if not self.binding or not self.history_dropped:
            return None
        n = self.history_dropped
        return Problem(
            f"{_plural(n, 'earlier answer', 'earlier answers')} in this chat {'wasn' if n == 1 else 'weren'}'t used.",
            "They were written before a type of document was left out of Ask, so they may repeat details from it.",
            "Ask again if you still need them; the new answer only uses what is available now.", link="/privacy")


# --- the wiki, as Ask / the check may see it ---------------------------------------------------------

@dataclass(frozen=True)
class WikiView:
    hidden_claims: frozenset = frozenset()
    hidden_pages: frozenset = frozenset()
    trimmed_pages: frozenset = frozenset()       # visible pages that have at least one hidden claim

    def claim_visible(self, claim_id: int) -> bool:
        return claim_id not in self.hidden_claims

    def page_visible(self, page_id: int) -> bool:
        return page_id not in self.hidden_pages


# --- the reach itself -------------------------------------------------------------------------------

@dataclass
class Reach:
    binding: bool
    access: dict = field(default_factory=dict)          # (domain, category) -> TypeAccess
    tier: Optional[Tier] = None                           # level of every AI call (None = not binding)
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def excluded_keys(self) -> frozenset:
        return frozenset(type_key(a.domain, a.category) for a in self.access.values() if not a.included)

    # types
    def includes_type(self, domain: object, category: Optional[str]) -> bool:
        if not self.binding:
            return True
        if domain is None or category is None:
            return False                                 # no type, no level: fail closed
        a = self.access.get((ps._s(domain), category))
        return a is not None and a.included

    def includes_document(self, doc: Optional[Document]) -> bool:
        return doc is not None and self.includes_type(doc.domain, doc.category)

    def includes_record(self, record: Optional[Record]) -> bool:
        return record is not None and self.includes_type(record.domain, record.category)

    def includes_source(self, source: object) -> bool:
        return self.includes_record(source) if isinstance(source, Record) else self.includes_document(source)

    # ids (None = no filtering; a document not in the set is excluded, so a newly filed one fails closed)
    def included_document_ids(self, session: Session) -> Optional[set]:
        if not self.binding:
            return None
        if "docs" not in self._cache:
            self._cache["docs"] = {i for i, d, c in session.exec(select(Document.id, Document.domain, Document.category))
                                   if self.includes_type(d, c)}
        return self._cache["docs"]

    def included_record_ids(self, session: Session) -> Optional[set]:
        if not self.binding:
            return None
        if "recs" not in self._cache:
            self._cache["recs"] = {i for i, d, c in session.exec(select(Record.id, Record.domain, Record.category))
                                   if self.includes_type(d, c)}
        return self._cache["recs"]

    def includes_file_of(self, session: Session, doc: Optional[Document]) -> bool:
        """May this document's file be shown to the AI? Its own type, and the type of the record it is attached to."""
        if doc is None:
            return False
        if not self.binding:
            return True
        if not self.includes_document(doc):
            return False
        attached = session.exec(select(Record).where(Record.document_id == doc.id)).first()
        return attached is None or self.includes_record(attached)

    def includes_todo(self, session: Session, todo: "Todo") -> bool:
        if not self.binding:
            return True
        if todo.document_id is not None and todo.document_id not in self.included_document_ids(session):
            return False
        if todo.record_id is not None and todo.record_id not in self.included_record_ids(session):
            return False
        if todo.transaction_id is not None:
            txn = session.get(Transaction, todo.transaction_id)
            if txn is None or txn.document_id not in self.included_document_ids(session):
                return False
        return True

    def filter_entries(self, entries: "Iterable[SourceEntry]") -> "list[SourceEntry]":
        """Settled entries the AI may see. A record whose attached file is of an excluded type stays,
        without the file (its title would otherwise be the file's name)."""
        if not self.binding:
            return list(entries)
        out = []
        for e in entries:
            if e.record is not None:
                if not self.includes_record(e.record):
                    continue
                if e.document is not None and not self.includes_document(e.document):
                    e = replace(e, document=None)
            elif not self.includes_document(e.document):
                continue
            out.append(e)
        return out

    # the wiki
    def wiki_view(self, session: Session) -> WikiView:
        if not self.binding:
            return WikiView()
        doc_ok, rec_ok = self.included_document_ids(session), self.included_record_ids(session)
        bad = set()
        for link in session.exec(select(WikiClaimSource).where(WikiClaimSource.withdrawn_at.is_(None))):
            ok = (link.record_id in rec_ok) if link.record_id is not None else (link.document_id in doc_ok)
            if not ok:
                bad.add(link.claim_id)
        by_page = defaultdict(list)
        for claim_id, page_id in session.exec(select(WikiClaim.id, WikiClaim.page_id)):
            by_page[page_id].append(claim_id)
        hidden_pages = {p for p, cs in by_page.items() if cs and all(c in bad for c in cs)}
        trimmed = {p for p, cs in by_page.items() if any(c in bad for c in cs)} - hidden_pages
        return WikiView(frozenset(bad), frozenset(hidden_pages), frozenset(trimmed))

    # what was left out
    def left_out(self, session: Session, history_dropped: int = 0) -> LeftOut:
        if not self.binding:
            return LeftOut()
        from app.services.ask.tools import settled_entries      # local: tools imports this module

        counts: dict[tuple, list] = {}

        def bump(domain: object, category: Optional[str], is_record: bool) -> None:
            c = counts.setdefault((ps._s(domain), category), [0, 0])
            c[1 if is_record else 0] += 1

        for e in settled_entries(session):
            if e.record is not None:
                if not self.includes_record(e.record):
                    bump(e.record.domain, e.record.category, True)
                elif e.document is not None and not self.includes_document(e.document):
                    bump(e.document.domain, e.document.category, False)
            elif not self.includes_document(e.document):
                bump(e.document.domain, e.document.category, False)
        types = []
        for (domain, category), (n_docs, n_recs) in sorted(counts.items(), key=lambda kv: str(kv[0])):
            a = self.access.get((domain, category))
            types.append(LeftOutType(a.label if a else (category or "Unknown kind of document"),
                                     a.reason if a and a.reason else UNSET, n_docs, n_recs))
        return LeftOut(True, tuple(sorted(self.excluded_keys)), tuple(types), history_dropped)


def compute_reach(session: Session) -> Reach:
    if not ps.enforcing(session):
        return Reach(binding=False)
    saved = {(r.domain, r.category): r for r in session.exec(select(DocumentTypeSetting))}
    access = {}
    for info in ps.known_types():
        row = saved.get((info.domain, info.category))
        tier: Optional[Tier] = None
        if row is not None and row.tier is not None:
            try:
                tier = parse_tier(row.tier)
            except TierBlocked:
                tier = None                              # a garbled level counts as "no level"
        reason = None
        if tier is None:
            reason = UNSET
        elif tier == Tier.HIGH:
            reason = HIGH
        elif row.redact:
            reason = HIDES
        access[(info.domain, info.category)] = TypeAccess(info.domain, info.category, info.label,
                                                          reason is None, reason, tier)
    levels = [a.tier for a in access.values() if a.included]
    call_tier = max(levels, key=_ORDER.__getitem__) if levels else Tier.MID
    return Reach(binding=True, access=access, tier=call_tier)


_current: contextvars.ContextVar = contextvars.ContextVar("hub_privacy_reach", default=None)


def current_reach() -> Optional[Reach]:
    return _current.get()


def reach_for(session: Session) -> Reach:
    """The snapshot of the run in progress, or - for a tool called outside a run - a fresh one."""
    return _current.get() or compute_reach(session)
