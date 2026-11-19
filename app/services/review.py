"""The one review queue, "Waiting for you" (PLAN step 3, C1).

Producers open items (`open_item`); a person answers on /waiting; the item's kind decides what the answer does
(`HANDLERS`). Every decision stamps who and when. Snoozing puts an item away for 7 days (default taken
2026-11-19). The menu shows one count (`menu_count`); the page filters by area."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import or_
from sqlmodel import Session, select

from app.models.review import APPROVED, DISCARDED, OPEN, SNOOZED, WAITING, ReviewItem

SNOOZE_DAYS = 7
AREAS = {"money": "Money", "health": "Health", "inbox": "Inbox", "house": "House", "other": "Other"}
APPROVE, DISCARD, SNOOZE = "approve", "discard", "snooze"


class ReviewError(Exception):
    """A decision that could not be taken; `what`, `why`, `next_step` in plain words."""

    def __init__(self, what: str, why: str, next_step: str):
        self.what, self.why, self.next_step = what, why, next_step
        super().__init__(f"{what} {why} {next_step}")


def options(item: ReviewItem) -> list[dict]:
    try:
        return list(json.loads(item.options_json or "[]"))
    except ValueError:
        return []


def open_item(session: Session, *, area: str, kind: str, ref: str, title: str, why: str,
              evidence_url: Optional[str] = None, options: Optional[list[dict]] = None) -> ReviewItem:
    """The open item for this thing, made if there is none. Does not commit."""
    item = session.exec(select(ReviewItem).where(ReviewItem.kind == kind, ReviewItem.ref == ref,
                                                 ReviewItem.status.in_(OPEN))).first()
    if item is None:
        item = ReviewItem(area=area, kind=kind, ref=ref, title=title, why=why, evidence_url=evidence_url,
                          options_json=json.dumps(options or []))
        session.add(item)
        session.flush()
    return item


def _waiting_query(now: datetime, area: Optional[str] = None):
    q = select(ReviewItem).where(or_(ReviewItem.status == WAITING,
                                     (ReviewItem.status == SNOOZED) & (ReviewItem.snoozed_until <= now)))
    return q.where(ReviewItem.area == area) if area else q


PAGE = 50


def waiting(session: Session, area: Optional[str] = None, now: Optional[datetime] = None, *,
            offset: int = 0, limit: Optional[int] = None) -> list[ReviewItem]:
    sync(session)
    q = _waiting_query(now or datetime.utcnow(), area).order_by(ReviewItem.created_at, ReviewItem.id).offset(offset)
    return list(session.exec(q.limit(limit) if limit else q))


def count(session: Session, now: Optional[datetime] = None) -> int:
    return len(session.exec(_waiting_query(now or datetime.utcnow())).all())


def area_counts(session: Session) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in session.exec(_waiting_query(datetime.utcnow())):
        out[item.area] = out.get(item.area, 0) + 1
    return out


def menu_count() -> int:
    """For the menu. Opens its own session; never raises (a menu must not break a page)."""
    try:
        from app.db import engine
        with Session(engine) as s:
            return count(s)
    except Exception:
        return 0


# --- answering -------------------------------------------------------------------------------------

Handler = Callable[[Session, ReviewItem, str, bool, Optional[str]], str]   # -> status after the answer
HANDLERS: dict[str, Handler] = {}


def decide(session: Session, item_id: int, answer: str, *, by: Optional[str], remember: bool = False) -> ReviewItem:
    item = session.get(ReviewItem, item_id)
    if item is None or item.status not in OPEN:
        raise ReviewError("Nothing was changed.", "This item was already answered (perhaps by someone else).",
                          "Reload the page to see what is still waiting.")
    if answer == SNOOZE:
        item.status, item.snoozed_until = SNOOZED, datetime.utcnow() + timedelta(days=SNOOZE_DAYS)
    else:
        if answer not in {o["value"] for o in options(item)}:
            raise ReviewError("Nothing was changed.", "That answer is not one of this item's choices.",
                              "Pick one of the buttons shown on the item.")
        handler = HANDLERS.get(item.kind)
        if handler is None:
            raise ReviewError("Nothing was changed.", "The Hub no longer knows how to answer this kind of item.",
                              "Tell Pedro; the item stays on the list.")
        item.status = handler(session, item, answer, remember, by)
        item.answer, item.remembered = answer, remember
    item.decided_by, item.decided_at = by or "someone in the family", datetime.utcnow()
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


# --- producers ------------------------------------------------------------------------------------------

def sync(session: Session) -> None:
    """Producers that look at what is already stored (cheap, idempotent): run when the queue is read."""
    for producer in PRODUCERS:
        producer(session)
    session.commit()


def _ai_rules(session: Session) -> None:
    """Step 2's waiting rules: an AI answer saved switched off below the confidence bar waits for a person."""
    from app.models.category_node import CategoryNode
    from app.models.rule import AI, Rule
    from app.services.taxonomy import path_label
    answered = {i.ref for i in session.exec(select(ReviewItem).where(ReviewItem.kind == "ai_rule"))}
    for rule in session.exec(select(Rule).where(Rule.source == AI, Rule.enabled == False)):  # noqa: E712
        if f"rule:{rule.id}" not in answered:
            ai_rule_item(session, rule)


def ai_rule_item(session: Session, rule) -> Optional[ReviewItem]:
    """The item for one AI rule saved switched off (opened by the engine the moment it saves one). Does not commit."""
    from app.models.category_node import CategoryNode
    from app.services.taxonomy import path_label
    if rule.node_id is None:
        return None
    if rule.id is None:
        session.flush()
    node = session.get(CategoryNode, rule.node_id)
    sure = f"{rule.confidence:.0%} sure" if rule.confidence is not None else "unsure"
    return open_item(session, area="money", kind="ai_rule", ref=f"rule:{rule.id}",
                     title=f"{rule.merchant or rule.pattern}: is it {path_label(session, node) if node else 'this category'}?",
                     why=f"The AI suggested this category but was only {sure}, so the entries were left in Needs review "
                         f"instead of being filed on a guess (e.g. “{rule.example or rule.pattern}”).",
                     evidence_url=f"/transactions?q={(rule.merchant or rule.pattern)[:40]}",
                     options=[{"value": APPROVE, "label": "Yes, file them there"},
                              {"value": DISCARD, "label": "No"}])


def _answer_ai_rule(session: Session, item: ReviewItem, answer: str, remember: bool, by: Optional[str]) -> str:
    from app.models.category_node import CategoryNode
    from app.models.merchant import Merchant
    from app.models.rule import Rule
    from app.services.merchant_assign import assign_category
    rule = session.get(Rule, int(item.ref.split(":", 1)[1]))
    if rule is None:
        raise ReviewError("Nothing was changed.", "The suggestion this item was about no longer exists.",
                          "Discard the item, or reload the page.")
    if answer == DISCARD:
        return DISCARDED
    rule.enabled, rule.approved_by, rule.approved_at = True, by, datetime.utcnow()
    session.add(rule)
    merchant = session.exec(select(Merchant).where(Merchant.canonical_name == rule.merchant,
                                                   Merchant.merged_into_id.is_(None))).first()
    node = session.get(CategoryNode, rule.node_id)
    if merchant is not None and node is not None:
        # File its waiting entries there; "remember" makes it a person's rule too (it then outranks any AI answer).
        assign_category(session, [merchant.id], node, refile_existing=False, confirm=remember, by=by)
    return APPROVED


HANDLERS["ai_rule"] = _answer_ai_rule


# --- moved queues: each keeps in step with the page it came from ------------------------------------------------

def _open_refs(session: Session, kind: str) -> set[str]:
    return set(session.exec(select(ReviewItem.ref).where(ReviewItem.kind == kind, ReviewItem.status.in_(OPEN))))


def _close_gone(session: Session, kind: str, still_open: set[str], stamp: Callable[[str], tuple]) -> None:
    """Items whose thing was decided elsewhere (its own page, a script) close with that decision's who and when."""
    for item in session.exec(select(ReviewItem).where(ReviewItem.kind == kind, ReviewItem.status.in_(OPEN))):
        if item.ref not in still_open:
            status, by, at = stamp(item.ref)
            item.status, item.decided_by, item.decided_at = status, by or "decided elsewhere", at or datetime.utcnow()
            item.answer = "decided on its own page"
            session.add(item)


def _to_check_lines(session: Session) -> None:
    """Bank-statement lines the Hub was unsure about (decision D11/D17): held out of every total until answered."""
    from app.models.document import Document
    from app.models.transaction import Transaction
    from app.services import to_check
    held = session.exec(select(Transaction).where(Transaction.review_status == to_check.NEEDS_CHECK)).all()
    refs, existing = set(), _open_refs(session, "to_check_line")
    for t in held:
        ref = f"txn:{t.id}"
        refs.add(ref)
        if ref in existing:
            continue
        when = to_check.line_date(t)
        doc = session.get(Document, t.document_id)
        open_item(session, area="money", kind="to_check_line", ref=ref,
                  title=f"Is this bank line right? {t.description} · {to_check.money(t.amount)}"
                        f"{' · ' + when.isoformat() if when else ''}",
                  why=(t.review_reason or "The Hub was unsure it read this line correctly.")
                      + " It is left out of every total until you answer.",
                  evidence_url=f"/to-check#tc-line-{t.id}" if doc is not None else "/to-check",
                  options=[{"value": APPROVE, "label": "It's right"},
                           {"value": DISCARD, "label": "It isn't on the statement"}])

    def stamp(ref: str) -> tuple:
        t = session.get(Transaction, int(ref.split(":")[1]))
        if t is None:
            return DISCARDED, None, None
        return (APPROVED if t.review_status != "rejected" else DISCARDED), t.checked_by, t.checked_at
    _close_gone(session, "to_check_line", refs, stamp)


def _answer_to_check_line(session: Session, item: ReviewItem, answer: str, remember: bool, by: Optional[str]) -> str:
    from app.services import to_check
    tid = int(item.ref.split(":")[1])
    try:
        (to_check.confirm if answer == APPROVE else to_check.reject)(session, tid, by)
    except to_check.ToCheckError as exc:
        p = exc.problem
        raise ReviewError(p.what, p.why, p.next_step) from None
    return APPROVED if answer == APPROVE else DISCARDED


def _statement_mismatches(session: Session) -> None:
    from app.models.document import Document
    from app.models.transaction import StatementCheck
    from app.services import to_check
    open_checks = session.exec(select(StatementCheck).where(StatementCheck.status == "mismatch",
                                                            StatementCheck.resolved_at.is_(None))).all()
    refs = set()
    for c in open_checks:
        ref = f"doc:{c.document_id}"
        refs.add(ref)
        doc = session.get(Document, c.document_id)
        open_item(session, area="money", kind="statement_mismatch", ref=ref,
                  title=f"{doc.filename if doc else 'A statement'} doesn't add up",
                  why=to_check.mismatch_sentence(c), evidence_url="/to-check",
                  options=[{"value": APPROVE, "label": "Accept the difference"}])

    def stamp(ref: str) -> tuple:
        c = session.exec(select(StatementCheck).where(StatementCheck.document_id == int(ref.split(":")[1]))).first()
        return (APPROVED, c.resolved_by, c.resolved_at) if c is not None else (DISCARDED, None, None)
    _close_gone(session, "statement_mismatch", refs, stamp)


def _answer_statement_mismatch(session: Session, item: ReviewItem, answer: str, remember: bool, by: Optional[str]) -> str:
    from app.services import to_check
    try:
        to_check.accept_difference(session, int(item.ref.split(":")[1]), by)
    except to_check.ToCheckError as exc:
        p = exc.problem
        raise ReviewError(p.what, p.why, p.next_step) from None
    return APPROVED


def _merchants(session: Session) -> None:
    """Needs Review's new merchants: a merchant the Hub made that no person has confirmed yet."""
    from app.models.category_node import CategoryNode
    from app.models.merchant import Merchant
    from app.services.taxonomy import path_label
    rows = session.exec(select(Merchant).where(Merchant.confirmed == False, Merchant.merged_into_id.is_(None))).all()  # noqa: E712
    from app.models.rule import Rule
    refs, existing = set(), _open_refs(session, "merchant")
    asked = {r.merchant for r in (session.get(Rule, int(x.split(":")[1])) for x in _open_refs(session, "ai_rule"))
             if r is not None and r.merchant}                      # one question per thing: its AI answer is waiting
    for m in rows:
        ref = f"merchant:{m.id}"
        if m.canonical_name in asked:
            continue
        refs.add(ref)
        if ref in existing:
            continue
        node = session.get(CategoryNode, m.default_category_id) if m.default_category_id else None
        known = node is not None and not node.slug.startswith("unsorted") and node.level == 3
        open_item(session, area="money", kind="merchant", ref=ref,
                  title=(f"{m.canonical_name}: is it {path_label(session, node)}?" if known
                         else f"{m.canonical_name}: which category?"),
                  why=("The Hub filed this new merchant itself; no one has confirmed it yet." if known else
                       "The Hub couldn't tell what this new merchant is, so its entries wait in Needs review."),
                  evidence_url="/financials/transactions/needs-review",
                  options=[{"value": APPROVE, "label": "Yes, that's right"}] if known else [])

    def stamp(ref: str) -> tuple:
        m = session.get(Merchant, int(ref.split(":")[1]))
        return (APPROVED if m is not None and m.confirmed else DISCARDED), None, None
    _close_gone(session, "merchant", refs, stamp)


def _answer_merchant(session: Session, item: ReviewItem, answer: str, remember: bool, by: Optional[str]) -> str:
    from app.models.category_node import CategoryNode
    from app.models.merchant import Merchant
    from app.services.merchant_assign import assign_category
    m = session.get(Merchant, int(item.ref.split(":")[1]))
    node = session.get(CategoryNode, m.default_category_id) if m is not None and m.default_category_id else None
    if m is None or node is None:
        raise ReviewError("Nothing was changed.", "This merchant has no category to confirm any more.",
                          "Choose its category on the Needs review page.")
    assign_category(session, [m.id], node, refile_existing=False, confirm=True, by=by) if remember else None
    m.confirmed = True
    session.add(m)
    return APPROVED


HANDLERS["merchant"] = _answer_merchant
HANDLERS["to_check_line"] = _answer_to_check_line
HANDLERS["statement_mismatch"] = _answer_statement_mismatch
PRODUCERS: list[Callable[[Session], None]] = [_ai_rules, _to_check_lines, _statement_mismatches, _merchants]


@dataclass
class Card:
    item: ReviewItem
    options: list[dict]
    area_label: str


def card(item: ReviewItem) -> Card:
    return Card(item, options(item), AREAS.get(item.area, item.area.title()))
