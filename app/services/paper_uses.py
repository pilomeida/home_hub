"""Where a paper is used (work-list B7, Pedro 2026-11-04): one copy of each paper, linked from wherever the Hub needs
it, and no voids. Every path that takes a paper out of use (Health's delete, the Inbox discard, any new one) first asks
`refuse_if_used`: a paper still used anywhere it does not take back itself is refused, with the list of places, each
a link. It can be taken out of use once nothing else uses it.

Every table with a link to `documents.id` is either read here (`USE_TABLES`) or named as history (`HISTORY_TABLES`):
tests/test_paper_uses.py fails when a new link is in neither, so a new use cannot be missed."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from app.categories.base import FiledAs, UnknownKindError
from app.categories.registry import document_url, get_spec, is_implemented, record_url
from app.models.debt import Debt
from app.models.document import Document
from app.models.category import Category
from app.models.health import HealthEntry
from app.models.position import (BalanceSnapshot, LoanAlert, LoanMovement, LoanSnapshot, PositionExtraction,
                                 SavingsSnapshot)
from app.models.record import Record
from app.models.todo import Todo
from app.models.transaction import StatementCheck, Transaction
from app.models.utility_reading import UtilityReading
from app.models.wiki import ClaimStatus, WikiClaim, WikiClaimSource, WikiPage
from app.problems import Problem

# Areas a use belongs to: the places a kind's reading writes (FiledAs, declared on each kind, work-list B2). A path
# that takes a paper out of use takes back what its kind files as (`filed_as`), and nothing else.
MONEY, LOANS, RECORDS, HEALTH, TODOS, WIKI, INSURANCE = (a.value for a in (
    FiledAs.MONEY, FiledAs.LOANS, FiledAs.RECORDS, FiledAs.HEALTH, FiledAs.TODOS, FiledAs.WIKI, FiledAs.INSURANCE))

USE_TABLES = frozenset({
    "transactions", "statement_checks", "utility_readings", "loan_movements", "loan_snapshots", "loan_alerts",
    "savings_snapshots", "balance_snapshots", "position_extractions", "records", "health_entries", "todos",
    "wiki_claim_sources",
})
# Links that only say what happened once (logs, a skipped duplicate, the Inbox card a paper waited on): not uses.
HISTORY_TABLES = frozenset({"inbox_items", "inbox_skips", "wiki_changes", "wiki_log"})


@dataclass(frozen=True)
class PaperUse:
    area: str
    label: str
    link: Optional[str]


class PaperInUse(Exception):
    def __init__(self, problem: Problem):
        super().__init__(problem.text)
        self.problem = problem


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _count(session: Session, model, document_id: int) -> int:
    return len(session.exec(select(model.id).where(model.document_id == document_id)).all())


def uses_of(session: Session, document: Document) -> list[PaperUse]:
    doc_id, own_page = document.id, document_url(document)
    uses: list[PaperUse] = []

    lines = _count(session, Transaction, doc_id)
    if lines:
        uses.append(PaperUse(MONEY, f"{_n(lines, 'bill or bank line', 'bill or bank lines')} read from it, in Money", own_page))
    if _count(session, StatementCheck, doc_id):
        uses.append(PaperUse(MONEY, "the check of this statement against the bank lines, in Money", own_page))
    readings = _count(session, UtilityReading, doc_id)
    if readings:
        uses.append(PaperUse(MONEY, f"{_n(readings, 'meter reading', 'meter readings')} read from it, in Utilities", own_page))
    balances = sum(_count(session, m, doc_id) for m in (SavingsSnapshot, BalanceSnapshot, PositionExtraction))
    if balances:
        uses.append(PaperUse(MONEY, f"{_n(balances, 'balance', 'balances')} read from it, in Money", own_page))

    per_debt = Counter(row.debt_id for model in (LoanMovement, LoanSnapshot, LoanAlert)
                       for row in session.exec(select(model).where(model.document_id == doc_id)).all())
    for debt_id, count in sorted(per_debt.items()):
        debt = session.get(Debt, debt_id)
        name = (debt.name if debt is not None and debt.name else f"loan {debt_id}")
        uses.append(PaperUse(LOANS, f"{_n(count, 'figure', 'figures')} of {name}, in Loans", f"/financials/loans/{debt_id}"))

    for record in session.exec(select(Record).where(Record.document_id == doc_id, Record.retired_at.is_(None))).all():
        uses.append(PaperUse(RECORDS, "attached to a record entered by hand", record_url(record)))
    if session.exec(select(HealthEntry.id).where(HealthEntry.document_id == doc_id)).first() is not None:
        uses.append(PaperUse(HEALTH, "the Health record read from it", f"/health/documents/{doc_id}"))
    for todo in session.exec(select(Todo).where(Todo.document_id == doc_id, Todo.done == False)).all():  # noqa: E712
        uses.append(PaperUse(TODOS, f"the open to-do “{todo.title}”", "/todos"))

    pages = session.exec(
        select(WikiPage).join(WikiClaim, WikiClaim.page_id == WikiPage.id)
        .join(WikiClaimSource, WikiClaimSource.claim_id == WikiClaim.id)
        .where(WikiClaimSource.document_id == doc_id, WikiClaimSource.withdrawn_at.is_(None),
               WikiClaim.status == ClaimStatus.ACTIVE)).all()
    for page_id, count in sorted(Counter(p.id for p in pages).items()):
        page = session.get(WikiPage, page_id)
        uses.append(PaperUse(WIKI, f"{_n(count, 'fact', 'facts')} on the wiki page “{page.topic}”", f"/wiki/{page_id}"))

    if document.category == Category.FINANCIALS:     # a premium notice is a bill shown on its policy's card
        from app.categories.insurance.policies import build_policy_cards
        for card in build_policy_cards(session):
            if any(p.entry.document is not None and p.entry.document.id == doc_id for p in card.documents):
                uses.append(PaperUse(INSURANCE, f"listed on the {card.insurer} policy {card.policy_number}, in Insurance",
                                     f"/insurance#{card.anchor}"))
    return uses


def filed_as(document: Document) -> frozenset[str]:
    """What reading this paper wrote, as its kind declares; for a paper whose kind is not known (any more), what any
    kind of its category may write."""
    if not is_implemented(document.category):
        return frozenset()
    spec = get_spec(document.category)
    try:
        declared = spec.kind(document.paper_kind).files_as
    except UnknownKindError:
        declared = frozenset().union(*(k.files_as for k in spec.kinds))
    return frozenset(a.value for a in declared)


def refuse_if_used(session: Session, document: Document, *, what: str, takes_back: frozenset[str] = frozenset()) -> None:
    """Raise PaperInUse when the paper is used anywhere the caller does not take back itself."""
    elsewhere = [u for u in uses_of(session, document) if u.area not in takes_back]
    if not elsewhere:
        return
    raise PaperInUse(Problem(
        what,
        f"The Hub keeps one copy of each paper, and this one is still used in "
        f"{_n(len(elsewhere), 'other place', 'other places')}: taking it away would leave "
        f"{'it' if len(elsewhere) == 1 else 'them'} pointing at nothing.",
        "Open each place below and take the paper out of it there (or keep the paper), then try again.",
        places=tuple((u.label, u.link) for u in elsewhere)))
