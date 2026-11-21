"""What Ask may do (PLAN step 10g, Pedro's yes 2026-11-21). Three tiers, the same for everyone:

1. **Undoable**: done at once, the answer shows Undo (add a to-do; with a date it is the reminder).
2. **Confirm first**: changes what the family sees, so the answer shows a confirm card and nothing happens until a
   person presses "Do it" (mark a bill paid, approve the Inbox papers the Hub is sure of, give a merchant its
   category). Each confirmed change is made by the same code the page uses.
3. **Never from Ask**: deleting, privacy levels, bank connections. There is no tool for them; the system prompt
   tells the AI to point to the page.

Every action is one `AskAction` row on the turn that made it, so Undo and Confirm work after a reload and the card
shows what came of it. The tools read nothing the AI is not already allowed to read (app/privacy/reach.py): a bill
outside the run's reach answers exactly like a missing one, the Inbox proposal returns only a count, and the card
itself (rendered by the server for the person, not shown to the AI) lists the details."""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from sqlmodel import Session, select

from app.models.ask import AskAction
from app.models.category import Category
from app.models.document import Document, DocumentStatus
from app.models.merchant import Merchant
from app.models.todo import Todo
from app.models.transaction import Transaction
from app.privacy.reach import reach_for
from app.problems import Problem
from app.services.ask.contracts import AskTool, ToolOutput
from app.services.ask.refs import citable_for_todo
from app.services.names import fold

UNDOABLE, CONFIRM = "undoable", "confirm"
DONE, UNDONE, PROPOSED, CONFIRMED, CANCELLED, FAILED = "done", "undone", "proposed", "confirmed", "cancelled", "failed"
ADD_TODO, MARK_PAID, APPROVE_INBOX, MERCHANT_CATEGORY = "add_todo", "mark_bill_paid", "approve_inbox", "merchant_category"
KINDS = {ADD_TODO: UNDOABLE, MARK_PAID: CONFIRM, APPROVE_INBOX: CONFIRM, MERCHANT_CATEGORY: CONFIRM}

# The turn being answered: the engine sets it around its tool loop, so an action knows which answer made it.
current_turn: ContextVar[Optional[int]] = ContextVar("ask_current_turn", default=None)


class ActionRefused(Exception):
    """Undo, Confirm or Cancel could not be done; carries the three-part message for the person."""

    def __init__(self, problem: Problem):
        super().__init__(problem.text)
        self.problem = problem


def _turn_id() -> int:
    turn_id = current_turn.get()
    if turn_id is None:
        raise RuntimeError("an Ask action needs the turn being answered")
    return turn_id


def _record(session: Session, kind: str, summary: str, params: dict, *, status: str,
            result: Optional[dict] = None) -> AskAction:
    turn_id = _turn_id()
    action = AskAction(turn_id=turn_id, kind=kind, tier=KINDS[kind], status=status, summary=summary,
                       params_json=json.dumps(params), result_json=json.dumps(result) if result is not None else None)
    session.add(action)
    session.commit()
    session.refresh(action)
    return action


def params(action: AskAction) -> dict:
    return json.loads(action.params_json or "{}")


def result(action: AskAction) -> dict:
    return json.loads(action.result_json or "{}")


# --- tier 1: done at once, with Undo -------------------------------------------------------------------------------

def _add_todo(session: Session, args: dict) -> ToolOutput:
    _turn_id()                                   # refused before anything is written
    title = (args.get("title") or "").strip()[:200]
    if not title:
        return ToolOutput(text="No title given, so no to-do was added.", is_error=True)
    try:
        due = date.fromisoformat(args["due_date"]) if args.get("due_date") else None
        category = Category(args["domain"]) if args.get("domain") else None
    except ValueError:
        return ToolOutput(text="The date must be yyyy-mm-dd and the domain one of the listed values.", is_error=True)
    todo = Todo(title=title, due_date=due, category=category)
    session.add(todo)
    session.commit()
    session.refresh(todo)
    when = f", due {due.isoformat()}" if due else ""
    _record(session, ADD_TODO, f"Added to-do “{title}”{when}.", {"title": title}, status=DONE,
            result={"todo_id": todo.id})
    return ToolOutput(text=f"Done: added todo:{todo.id} “{title}”{when}. The person sees it with an Undo button; "
                           "say in one sentence that it was added.", citables=[citable_for_todo(todo)])


def undo(session: Session, action: AskAction, *, by: Optional[str]) -> AskAction:
    if action.tier != UNDOABLE or action.status != DONE:
        raise ActionRefused(Problem("Not undone.", "This action was already undone, or it is not one Ask can undo.",
                                    "Make the change on its own page if it is still needed."))
    todo = session.get(Todo, result(action).get("todo_id"))
    if todo is not None and todo.done:
        raise ActionRefused(Problem("Not undone.", "The to-do was already ticked off, so someone has used it.",
                                    "Untick or remove it on To-Dos.", link="/todos"))
    if todo is not None:
        session.delete(todo)                     # the row Ask itself made a moment ago
    action.status, action.decided_at, action.decided_by = UNDONE, datetime.utcnow(), by
    session.add(action)
    session.commit()
    return action


# --- tier 2: proposed, a person confirms ---------------------------------------------------------------------------

def _unpaid_lines(session: Session, document_id: int) -> list[Transaction]:
    return session.exec(select(Transaction).where(Transaction.document_id == document_id,
                                                  Transaction.paid_date.is_(None))).all()


def _bill(session: Session, document_id) -> Optional[Document]:
    """A filed bill the run may read, or None (a withheld bill answers exactly like a missing one)."""
    try:
        doc = session.get(Document, int(document_id))
    except (TypeError, ValueError):
        return None
    if (doc is None or doc.paper_kind != "bill" or doc.status != DocumentStatus.PROCESSED
            or not reach_for(session).includes_document(doc)):
        return None
    return doc


def _propose_mark_paid(session: Session, args: dict) -> ToolOutput:
    doc = _bill(session, args.get("document_id"))
    if doc is None:
        return ToolOutput(text="That bill is not available (not found, or not a filed bill).", is_error=True)
    if not _unpaid_lines(session, doc.id):
        return ToolOutput(text=f"doc:{doc.id} shows as paid already; nothing to propose.", is_error=True)
    _record(session, MARK_PAID, f"Mark {doc.filename} as paid.", {"document_id": doc.id}, status=PROPOSED)
    reach = reach_for(session)
    return ToolOutput(text=f"A confirm card for marking doc:{doc.id} paid is shown under the answer. Nothing has "
                           "changed yet: say it will be marked paid once the person confirms.",
                      tier=reach.private_tier([doc]), opened="1 private bill" if reach.private_tier([doc]) else "")


def _propose_approve_inbox(session: Session, args: dict) -> ToolOutput:
    from app.services import inbox_service
    sure = [e.document.id for e in inbox_service.pending_entries(session) if e.confident]
    if not sure:
        return ToolOutput(text="The Inbox has no paper the Hub is sure about; papers waiting for a choice are filed "
                               "on the Inbox page.", is_error=True)
    _record(session, APPROVE_INBOX, f"File {len(sure)} Inbox paper{'s' if len(sure) != 1 else ''} as the Hub suggests.",
            {"document_ids": sure}, status=PROPOSED)
    return ToolOutput(text=f"A confirm card listing {len(sure)} Inbox paper(s) the Hub is sure about is shown under "
                           "the answer. Nothing is filed until the person confirms.")


def _merchant(session: Session, name: str) -> Optional[Merchant]:
    wanted = fold(name or "").strip()
    if not wanted:
        return None
    for merchant in session.exec(select(Merchant).where(Merchant.merged_into_id.is_(None))).all():
        if fold(merchant.canonical_name).strip() == wanted:
            return merchant
    return None


def _propose_merchant_category(session: Session, args: dict) -> ToolOutput:
    from sqlalchemy.exc import NoResultFound

    from app.services.taxonomy import get_node, path_label
    merchant = _merchant(session, args.get("merchant", ""))
    if merchant is None:
        return ToolOutput(text="No merchant by that exact name; ask the person for the name as the Hub shows it.",
                          is_error=True)
    try:
        node = get_node(session, args.get("category", ""))
    except NoResultFound:
        node = None
    if node is None or node.level != 3 or node.slug.startswith("unsorted"):
        return ToolOutput(text="Pick a real sub-category slug (three levels, not Unsorted).", is_error=True)
    label = path_label(session, node)
    _record(session, MERCHANT_CATEGORY, f"File {merchant.canonical_name} under {label} from now on.",
            {"merchant_id": merchant.id, "node_slug": node.slug}, status=PROPOSED)
    return ToolOutput(text=f"A confirm card to file {merchant.canonical_name} under {label} is shown under the "
                           "answer. Nothing changes until the person confirms.")


@dataclass
class Outcome:
    done: list[str] = field(default_factory=list)          # plain lines: what was changed
    not_done: list[tuple[str, str, Optional[str]]] = field(default_factory=list)   # (what, why and what to do, link)


def _pending(action: AskAction) -> None:
    if action.tier != CONFIRM or action.status != PROPOSED:
        raise ActionRefused(Problem("Nothing to do.", "This proposal was already confirmed or cancelled.",
                                    "Ask again if it is still needed."))


async def confirm(session: Session, action: AskAction, *, by: Optional[str]) -> AskAction:
    _pending(action)
    outcome = Outcome()
    p = params(action)
    if action.kind == MARK_PAID:
        _do_mark_paid(session, p["document_id"], outcome)
    elif action.kind == APPROVE_INBOX:
        await _do_approve_inbox(session, p["document_ids"], by, outcome)
    elif action.kind == MERCHANT_CATEGORY:
        _do_merchant_category(session, p["merchant_id"], p["node_slug"], by, outcome)
    action = session.get(AskAction, action.id)
    action.status = CONFIRMED if outcome.done else FAILED
    action.result_json = json.dumps({"done": outcome.done, "not_done": outcome.not_done})
    action.decided_at, action.decided_by = datetime.utcnow(), by
    session.add(action)
    session.commit()
    return action


def cancel(session: Session, action: AskAction, *, by: Optional[str]) -> AskAction:
    _pending(action)
    action.status, action.decided_at, action.decided_by = CANCELLED, datetime.utcnow(), by
    session.add(action)
    session.commit()
    return action


def _do_mark_paid(session: Session, document_id: int, outcome: Outcome) -> None:
    """As ticking the bill's to-do on To-Dos: a bill is unpaid while its to-do is open (app/services/coming_up.py)."""
    doc = session.get(Document, document_id)
    lines = _unpaid_lines(session, document_id) if doc is not None else []
    if not lines:
        outcome.not_done.append((f"{doc.filename if doc else 'The bill'} was not marked paid.",
                                 "It no longer shows as unpaid, so there is nothing to mark.", None))
        return
    for txn in lines:
        todo = session.exec(select(Todo).where(Todo.transaction_id == txn.id)).first()
        if todo is None:
            todo = Todo(title=f"Pay {txn.description}", due_date=txn.due_date, transaction_id=txn.id,
                        document_id=document_id, category=doc.category)
        todo.done = True
        session.add(todo)
    session.commit()
    outcome.done.append(f"{doc.filename} marked paid (its to-do ticked off).")


async def _do_approve_inbox(session: Session, document_ids: list[int], by: Optional[str], outcome: Outcome) -> None:
    from app.categories.fields import InvalidClassification
    from app.privacy.settings import TierHeld
    from app.services import inbox_service
    for document_id in document_ids:
        entry = inbox_service.entry_for(session, document_id)
        name = entry.document.filename if entry else f"Paper {document_id}"
        card = f"/inbox/{document_id}/card"
        if entry is None or entry.document.status != DocumentStatus.PENDING_REVIEW:
            outcome.not_done.append((f"{name} was not filed.", "Someone already handled it in the Inbox.", "/inbox"))
            continue
        try:
            await inbox_service.approve(session, document_id, category_value=entry.item.suggested_category or "",
                                        paper_kind=entry.item.suggested_paper_kind, fields={}, reviewed_by=by)
        except InvalidClassification as exc:
            needs = ", ".join(sorted(exc.errors)) or "a detail"
            outcome.not_done.append((f"{name} was not filed.", f"It needs {needs} filled in. Open its card to add it.",
                                     card))
            continue
        except TierHeld:
            outcome.not_done.append((f"{name} was not filed.", "This kind of paper has no privacy level yet. Set it on "
                                     "the Privacy page, then approve it from its card.", "/privacy"))
            continue
        except inbox_service.InboxError as exc:
            outcome.not_done.append((f"{name} was not filed.", str(exc), "/inbox"))
            continue
        outcome.done.append(f"{name} filed as {entry.suggestion_label}.")


def _do_merchant_category(session: Session, merchant_id: int, node_slug: str, by: Optional[str],
                          outcome: Outcome) -> None:
    """As the merchants page: the merchant's category changes, its unsorted lines are filed, the answer is a rule."""
    from app.services.merchant_assign import assign_category
    from app.services.taxonomy import get_node, path_label
    merchant, node = session.get(Merchant, merchant_id), get_node(session, node_slug)
    report = assign_category(session, [merchant.id], node, refile_existing=False, confirm=True, by=by)
    lines = report.filed_from_unsorted
    outcome.done.append(f"{merchant.canonical_name} is filed under {path_label(session, node)} from now on"
                        + (f"; {lines} unsorted line{'s' if lines != 1 else ''} filed there." if lines else "."))


# --- what the card shows (rendered for the person by the server; never sent to the AI) -----------------------------

def card_rows(session: Session, action: AskAction) -> list[list[str]]:
    """The lines a confirm card lists: exactly what will change."""
    p = params(action)
    if action.kind == MARK_PAID:
        return [[t.description, f"€{t.amount:,.2f}", t.due_date.isoformat() if t.due_date else "no due date"]
                for t in _unpaid_lines(session, p["document_id"])]
    if action.kind == APPROVE_INBOX:
        from app.services import inbox_service
        rows = []
        for document_id in p["document_ids"]:
            entry = inbox_service.entry_for(session, document_id)
            if entry is not None:
                rows.append([entry.document.filename, entry.suggestion_label or "-",
                             _level(session, entry.item.suggested_category, entry.item.suggested_paper_kind)])
        return rows
    return []


def _level(session: Session, category: Optional[str], paper_kind: Optional[str]) -> str:
    from app.privacy.settings import configured_tier
    tier = configured_tier(session, category, paper_kind)
    return {"house_low": "Low", "house_mid": "Mid", "house_high": "High"}.get(tier or "", "No level yet")


def card_headers(action: AskAction) -> list[str]:
    return {MARK_PAID: ["Line", "Amount", "Due"], APPROVE_INBOX: ["Paper", "Filed as", "Level"]}.get(action.kind, [])


def for_turn(session: Session, turn_id: int) -> list[AskAction]:
    return list(session.exec(select(AskAction).where(AskAction.turn_id == turn_id).order_by(AskAction.id)).all())


def action_tools() -> list[AskTool]:
    from app.categories.registry import implemented_categories
    domain = {"type": "string", "enum": [s.category.value for s in implemented_categories()]}
    return [
        AskTool(name="add_todo",
                description="Add a household to-do at once (the person gets Undo). Give due_date for a reminder. "
                            "Use only when the person asks to add, note or be reminded of something.",
                input_schema={"type": "object", "properties": {
                    "title": {"type": "string"}, "due_date": {"type": "string", "description": "yyyy-mm-dd"},
                    "domain": domain}, "required": ["title"]},
                run=_add_todo),
        AskTool(name="propose_mark_bill_paid",
                description="Propose marking one filed bill (doc:N) as paid. Shows a confirm card; nothing changes "
                            "until the person confirms.",
                input_schema={"type": "object", "properties": {"document_id": {"type": "integer"}},
                              "required": ["document_id"]},
                run=_propose_mark_paid),
        AskTool(name="propose_approve_inbox",
                description="Propose filing every Inbox paper the Hub is sure about, as suggested. Shows a confirm "
                            "card listing them; nothing is filed until the person confirms.",
                input_schema={"type": "object", "properties": {}},
                run=_propose_approve_inbox),
        AskTool(name="propose_merchant_category",
                description="Propose filing a merchant (its exact name) under a spending sub-category slug from now "
                            "on. Shows a confirm card; nothing changes until the person confirms.",
                input_schema={"type": "object", "properties": {
                    "merchant": {"type": "string"}, "category": {"type": "string"}},
                    "required": ["merchant", "category"]},
                run=_propose_merchant_category),
    ]
