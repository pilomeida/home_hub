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
MERCHANT, MERCHANTS = "merchant", "merchants"     # the item kind, and the queue's own line for it (step 10c)


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


def _waiting_query(now: datetime, area: Optional[str] = None, merchants: Optional[bool] = None):
    """`merchants`: None = every kind; False = without the unconfirmed merchants; True = only them."""
    q = select(ReviewItem).where(or_(ReviewItem.status == WAITING,
                                     (ReviewItem.status == SNOOZED) & (ReviewItem.snoozed_until <= now)))
    if merchants is not None:
        q = q.where(ReviewItem.kind == MERCHANT) if merchants else q.where(ReviewItem.kind != MERCHANT)
    return q.where(ReviewItem.area == area) if area else q


PAGE = 50


def waiting(session: Session, area: Optional[str] = None, now: Optional[datetime] = None, *,
            offset: int = 0, limit: Optional[int] = None, merchants: Optional[bool] = None) -> list[ReviewItem]:
    sync(session)
    q = _waiting_query(now or datetime.utcnow(), area, merchants).order_by(ReviewItem.created_at, ReviewItem.id).offset(offset)
    return list(session.exec(q.limit(limit) if limit else q))


def count(session: Session, now: Optional[datetime] = None, merchants: Optional[bool] = None) -> int:
    return len(session.exec(_waiting_query(now or datetime.utcnow(), merchants=merchants)).all())


def merchant_count(session: Session) -> int:
    return count(session, merchants=True)


def area_counts(session: Session, merchants: Optional[bool] = None) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in session.exec(_waiting_query(datetime.utcnow(), merchants=merchants)):
        out[item.area] = out.get(item.area, 0) + 1
    return out


def menu_count() -> int:
    """For the menu: everything waiting except the unconfirmed merchants, which are their own line in the queue (step
    10c; they would drown the rest). Opens its own session; never raises (a menu must not break a page)."""
    try:
        from app.db import engine
        with Session(engine) as s:
            sync(s)                  # a line held a minute ago counts now, not only once someone opens the page
            return count(s, merchants=False)
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
                     evidence_url=f"/financials/transactions?q={(rule.merchant or rule.pattern)[:40]}",
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


def _flagged(session: Session, *, kind: str, area: str, model, flag: str, title: Callable, why: str, url: str,
             label: str) -> None:
    """Rows a person approves by setting one flag (Health areas, people, topics): one item each while it is unset."""
    rows = [r for r in session.exec(select(model).where(getattr(model, flag) == False))  # noqa: E712
            if getattr(r, "merged_into_id", None) is None and getattr(r, "kind", "area") == "area"]
    refs, existing = set(), _open_refs(session, kind)
    for r in rows:
        ref = f"{kind}:{r.id}"
        refs.add(ref)
        if ref not in existing:
            open_item(session, area=area, kind=kind, ref=ref, title=title(r), why=why, evidence_url=url,
                      options=[{"value": APPROVE, "label": label}])

    def stamp(ref: str) -> tuple:
        r = session.get(model, int(ref.split(":")[1]))
        return (APPROVED if r is not None and getattr(r, flag) else DISCARDED), None, None
    _close_gone(session, kind, refs, stamp)


def _health(session: Session) -> None:
    from app.models.health import HealthArea, HealthPerson, HealthTopic
    _flagged(session, kind="health_area", area="health", model=HealthArea, flag="approved",
             title=lambda a: f"New Health area proposed: {a.name}",
             why="The AI proposed it while reading a Health paper; it is used only once a person approves it.",
             url="/health/areas", label="Approve it")
    _flagged(session, kind="health_person", area="health", model=HealthPerson, flag="confirmed",
             title=lambda p: f"New person on a Health paper: {p.name}",
             why="A Health paper named someone the Hub didn't know; confirm it is a new person (or merge it on the "
                 "Records page if it is someone already there).", url="/health/records", label="Yes, a new person")
    _flagged(session, kind="health_topic", area="health", model=HealthTopic, flag="approved",
             title=lambda t: f"New Health topic proposed: {t.name}",
             why="A written report's headings named a topic the Hub didn't have; it is used once a person approves it.",
             url="/health/topics", label="Approve it")


def _health_conditions(session: Session) -> None:
    """PLAN step 7a: a condition the reader named that the person does not have yet (create it, or not a condition),
    and a paper the Hub thinks is about one of the person's conditions (yes, or no)."""
    from app.categories.health import conditions
    refs, existing = set(), _open_refs(session, "health_condition_new")
    for p in conditions.proposals(session):
        refs.add(p.ref)
        if p.ref not in existing:
            n = len(p.papers)
            open_item(session, area="health", kind="health_condition_new", ref=p.ref,
                      title=f"A condition for {p.person.name}? {p.name}",
                      why=f"The reader named it on {n} of {p.person.name}'s paper{'s' if n != 1 else ''}. A condition "
                          f"gets its own page with its papers; nothing is created until you say so.",
                      evidence_url=f"/health/documents/{_document_of_entry(session, p.papers[0])}",
                      options=[{"value": APPROVE, "label": "Create it"}, {"value": DISCARD, "label": "Not a condition"}])
    _close_gone(session, "health_condition_new", refs, lambda ref: (APPROVED, None, None))
    refs, existing = set(), _open_refs(session, "health_condition_paper")
    for entry, condition in conditions.open_suggestions(session):
        ref = f"{entry.id}:{condition.id}"
        refs.add(ref)
        if ref not in existing:
            open_item(session, area="health", kind="health_condition_paper", ref=ref,
                      title=f"Is this paper about {condition.name}?",
                      why="Its names fit this condition of the person; confirm it and it is listed on the condition's "
                          "page.", evidence_url=f"/health/documents/{entry.document_id}",
                      options=[{"value": APPROVE, "label": "Yes, it is"}, {"value": DISCARD, "label": "No"}])
    _close_gone(session, "health_condition_paper", refs, lambda ref: (APPROVED, None, None))


def _health_follow_ups(session: Session) -> None:
    """PLAN step 7b: a follow-up a paper's own words ask for ("repetir em 12 meses"): add it, or no."""
    from app.categories.health import follow_ups
    from app.models.health import HealthPerson
    refs, existing = set(), _open_refs(session, "health_follow_up")
    for f in follow_ups.proposals(session):
        ref = f"follow_up:{f.id}"
        refs.add(ref)
        if ref not in existing:
            person = session.get(HealthPerson, f.person_id)
            open_item(session, area="health", kind="health_follow_up", ref=ref,
                      title=f"Follow-up for {person.name if person else 'someone'} by {f.due_on.isoformat()}? {f.what}",
                      why=f"The paper says “{f.source_text}”, counted from its date. Added, it becomes a To-Do "
                          f"two weeks before.",
                      evidence_url=f"/health/documents/{_document_of_entry(session, f.entry_id)}" if f.entry_id else None,
                      options=[{"value": APPROVE, "label": "Add it"}, {"value": DISCARD, "label": "No"}])

    def stamp(ref: str) -> tuple:
        from app.models.health import HealthFollowUp
        f = session.get(HealthFollowUp, int(ref.split(":")[1]))
        return (APPROVED if f is not None and f.state == follow_ups.ACTIVE else DISCARDED), None, None
    _close_gone(session, "health_follow_up", refs, stamp)


def _answer_health_follow_up(session: Session, item: ReviewItem, answer: str, remember: bool, by: Optional[str]) -> str:
    from app.categories.health import follow_ups
    follow_up_id = int(item.ref.split(":")[1])
    try:
        if answer == APPROVE:
            follow_ups.accept(session, follow_up_id, by)
        else:
            follow_ups.dismiss(session, follow_up_id)
    except follow_ups.FollowUpError as exc:
        p = exc.problem
        raise ReviewError(p.what, p.why, p.next_step) from None
    return APPROVED if answer == APPROVE else DISCARDED


def _document_of_entry(session: Session, entry_id: int) -> Optional[int]:
    from app.models.health import HealthEntry
    entry = session.get(HealthEntry, entry_id)
    return entry.document_id if entry else None


def _answer_health_condition(session: Session, item: ReviewItem, answer: str, remember: bool, by: Optional[str]) -> str:
    from app.categories.health import conditions
    a, b = (int(x) for x in item.ref.split(":"))
    try:
        if item.kind == "health_condition_new":
            if answer == APPROVE:
                conditions.accept_proposal(session, a, b, by)
            else:
                conditions.dismiss_proposal(session, b)
        else:
            conditions.decide(session, a, b, conditions.CONFIRMED if answer == APPROVE else conditions.REJECTED, by)
    except conditions.ConditionError as exc:
        p = exc.problem
        raise ReviewError(p.what, p.why, p.next_step) from None
    return APPROVED if answer == APPROVE else DISCARDED


SAME = "same"


def _health_tests(session: Session) -> None:
    """A lab test the Hub had not seen (step 5b): approve it as new, or say it is the test it looks like."""
    from app.models.health import HealthTest
    rows = session.exec(select(HealthTest).where(HealthTest.approved == False,  # noqa: E712
                                                 HealthTest.merged_into_id.is_(None))).all()
    refs, existing = set(), _open_refs(session, "health_test")
    for t in rows:
        ref = f"health_test:{t.id}"
        refs.add(ref)
        if ref in existing:
            continue
        like = session.get(HealthTest, t.similar_to_id) if t.similar_to_id else None
        unit = f" ({t.unit})" if t.unit else ""
        options = [{"value": APPROVE, "label": "A test of its own"}]
        if like is not None:
            options.insert(0, {"value": SAME, "label": f"Same as {like.name}"})
        open_item(session, area="health", kind="health_test", ref=ref, title=f"New lab test: {t.name}{unit}",
                  why=("A lab sheet printed a test the Hub didn't know. Its results are kept either way; your answer "
                       "decides whether they share a line with a test already listed."
                       + (f" It looks like {like.name}." if like is not None else "")),
                  evidence_url="/health/people", options=options)

    def stamp(ref: str) -> tuple:
        t = session.get(HealthTest, int(ref.split(":")[1]))
        return (APPROVED if t is not None and (t.approved or t.merged_into_id) else DISCARDED), None, None
    _close_gone(session, "health_test", refs, stamp)


def _health_doubts(session: Session) -> None:
    """A Health paper whose reading the check could not settle (step 5c): its doubts, on one item, until a person has
    looked at the paper (the paper's own "I've checked these" does the same)."""
    from app.categories.health.store import notes_of
    from app.models.document import Document
    from app.models.health import HealthEntry
    refs, existing = set(), _open_refs(session, "health_doubts")
    for entry in session.exec(select(HealthEntry).where(HealthEntry.check_notes.is_not(None))).all():
        notes = notes_of(entry)
        doc = session.get(Document, entry.document_id)
        if not notes or doc is None:
            continue
        ref = f"health_entry:{entry.document_id}"
        refs.add(ref)
        if ref in existing:
            continue
        shown = "; ".join(notes[:3]) + (f"; and {len(notes) - 3} more" if len(notes) > 3 else "")
        open_item(session, area="health", kind="health_doubts", ref=ref,
                  title=f"Check the reading of {doc.filename}",
                  why=f"The check against the paper could not settle {len(notes)} point(s): {shown}",
                  evidence_url=f"/health/documents/{entry.document_id}",
                  options=[{"value": APPROVE, "label": "I looked at the paper"}])

    def stamp(ref: str) -> tuple:
        from app.categories.health.store import entry_for
        entry = entry_for(session, int(ref.split(":")[1]))
        return (APPROVED if entry is not None else DISCARDED), None, None
    _close_gone(session, "health_doubts", refs, stamp)


def _answer_health_doubts(session: Session, item: ReviewItem, answer: str, remember: bool, by: Optional[str]) -> str:
    from app.categories.health.store import clear_notes
    from app.models.document import Document
    doc = session.get(Document, int(item.ref.split(":")[1]))
    if doc is None:
        raise ReviewError("Nothing was changed.", "That Health paper is no longer filed.",
                          "Reload “Waiting for you”; the item will go away.")
    clear_notes(session, doc)
    return APPROVED


def _answer_health_test(session: Session, item: ReviewItem, answer: str, remember: bool, by: Optional[str]) -> str:
    from app.categories.health import tests
    from app.models.health import HealthTest
    test_id = int(item.ref.split(":")[1])
    try:
        if answer == SAME:
            t = session.get(HealthTest, test_id)
            if t is None or t.similar_to_id is None:
                raise ReviewError("Not merged.", "The test it looked like is no longer listed.",
                                  "Choose “A test of its own”, or merge it later on the person's results page.")
            tests.merge_tests(session, test_id, t.similar_to_id)
        else:
            tests.approve_test(session, test_id)
    except tests.TestError as exc:
        p = exc.problem
        raise ReviewError(p.what, p.why, p.next_step) from None
    return APPROVED


def _answer_health(session: Session, item: ReviewItem, answer: str, remember: bool, by: Optional[str]) -> str:
    from app.categories.health import areas, people, topics
    row_id = int(item.ref.split(":")[1])
    approve = {"health_area": areas.approve_area, "health_person": people.confirm_person,
               "health_topic": topics.approve_topic}[item.kind]
    try:
        approve(session, row_id)
    except Exception as exc:                       # the Health services raise their own three-part errors
        problem = getattr(exc, "problem", None)
        if problem is None:
            raise
        raise ReviewError(problem.what, problem.why, problem.next_step) from None
    return APPROVED


def _same_person(session: Session) -> None:
    """Inbox "Same person?" (work-list B4): two names that may be one person, asked, never guessed."""
    from app.models.subject import SubjectMatch
    from app.services import subjects
    refs, existing = set(), _open_refs(session, "same_person")
    for p in subjects.pending(session):
        ref = f"match:{p.match.id}"
        refs.add(ref)
        if ref not in existing:
            open_item(session, area="inbox", kind="same_person", ref=ref, title=f"Same person? {p.one} and {p.other}",
                      why="The two names are alike but not the same, so the Hub asks instead of guessing.",
                      evidence_url="/inbox", options=[{"value": APPROVE, "label": "Same person"},
                                                      {"value": DISCARD, "label": "Different people"}])

    def stamp(ref: str) -> tuple:
        m = session.get(SubjectMatch, int(ref.split(":")[1]))
        if m is None or m.decision is None:
            return DISCARDED, None, None
        return (APPROVED if m.decision == subjects.SAME else DISCARDED), m.decided_by, m.decided_at
    _close_gone(session, "same_person", refs, stamp)


def _answer_same_person(session: Session, item: ReviewItem, answer: str, remember: bool, by: Optional[str]) -> str:
    from app.services import subjects
    try:
        subjects.decide(session, int(item.ref.split(":")[1]), answer == APPROVE, by)
    except subjects.SubjectError as exc:
        p = exc.problem
        raise ReviewError(p.what, p.why, p.next_step) from None
    return APPROVED if answer == APPROVE else DISCARDED


for _kind in ("health_area", "health_person", "health_topic"):
    HANDLERS[_kind] = _answer_health
HANDLERS["same_person"] = _answer_same_person
HANDLERS["health_condition_new"] = _answer_health_condition
HANDLERS["health_condition_paper"] = _answer_health_condition
HANDLERS["health_follow_up"] = _answer_health_follow_up
HANDLERS["health_test"] = _answer_health_test
HANDLERS["health_doubts"] = _answer_health_doubts
HANDLERS["merchant"] = _answer_merchant
HANDLERS["to_check_line"] = _answer_to_check_line
HANDLERS["statement_mismatch"] = _answer_statement_mismatch
PRODUCERS: list[Callable[[Session], None]] = [_ai_rules, _to_check_lines, _statement_mismatches, _merchants,
                                               _health, _health_conditions, _health_follow_ups, _health_tests,
                                               _health_doubts,
                                               _same_person]


@dataclass
class Card:
    item: ReviewItem
    options: list[dict]
    area_label: str


def card(item: ReviewItem) -> Card:
    return Card(item, options(item), AREAS.get(item.area, item.area.title()))
