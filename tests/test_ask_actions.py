"""PLAN step 10g: Ask acts in three tiers. An undoable action is done at once and Undo takes it back; a change to what
the family sees waits on a confirm card and is made by the same code its page uses; deleting, privacy levels and
bank connections have no tool at all. Every action is a row on the turn that made it."""

import json
from datetime import date

import pytest
from sqlmodel import select

from app.models.ask import AskAction
from app.models.category import Category
from app.models.document import DocumentSource, DocumentStatus
from app.models.merchant import Merchant
from app.models.rule import PERSON, Rule
from app.models.todo import Todo
from app.models.transaction import Transaction, TransactionType
from app.services.ask import actions
from app.services.ask.conversation import start_conversation
from app.services.ask.engine import run_turn
from app.services.ask.tools import available_tools
from app.services.coming_up import _unpaid_bills
from app.services.taxonomy import get_node
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result, gateway_tool_result
from tests.knowledge_factories import make_document

TODAY = date(2026, 11, 21)


async def _ask(session, *calls, final="Done."):
    gw = FakeGateway([gateway_tool_result([{"id": f"t{i}", "name": n, "input": a} for i, (n, a) in enumerate(calls)]),
                      gateway_text_result(final)])
    turn = start_conversation(session, "please", started_by="rute@example.com")
    await run_turn(session, turn.id, gateway=gw, today=TODAY)
    return turn, gw


def _tool_text(gw, n=0):
    return gw.requests[1]["messages"][-1]["content"][n]["content"]


def _bill(session, *, due=date(2026, 12, 5)):
    doc = make_document(session, category=Category.FINANCIALS, paper_kind="bill", filename="edp-2026-11.pdf")
    session.add(Transaction(document_id=doc.id, description="EDP Comercial", amount=61.4, currency="EUR",
                            due_date=due, transaction_type=TransactionType.DEBIT))
    session.commit()
    return doc


def test_the_tiers_have_no_tool_for_deleting_privacy_or_bank_connections():
    names = {t.name for t in actions.action_tools()}
    assert names == {"add_todo", "propose_mark_bill_paid", "propose_approve_inbox", "propose_merchant_category"}
    every = " ".join(t.name + " " + t.description for t in available_tools()).lower()
    for never in ("delete", "remove", "privacy level", "bank connection", "discard"):
        assert never not in every, never
    assert set(actions.KINDS.values()) == {actions.UNDOABLE, actions.CONFIRM}


@pytest.mark.asyncio
async def test_a_todo_is_added_at_once_and_undo_takes_it_back(session, client):
    turn, gw = await _ask(session, ("add_todo", {"title": "Renew Rute's passport", "due_date": "2026-12-16"}))
    todo = session.exec(select(Todo)).one()
    assert (todo.title, todo.due_date, todo.done) == ("Renew Rute's passport", date(2026, 12, 16), False)
    [action] = actions.for_turn(session, turn.id)
    assert (action.tier, action.status, actions.result(action)) == ("undoable", "done", {"todo_id": todo.id})
    assert f"todo:{todo.id}" in _tool_text(gw)

    page = client.get(f"/ask/c/{turn.conversation_id}").text
    assert "Added to-do “Renew Rute&#39;s passport”, due 2026-12-16." in page and f"/ask/actions/{action.id}/undo" in page

    assert client.post(f"/ask/actions/{action.id}/undo", headers={"HX-Request": "true"}).status_code == 200
    session.expire_all()
    assert session.exec(select(Todo)).all() == [] and session.get(AskAction, action.id).status == "undone"
    again = client.post(f"/ask/actions/{action.id}/undo")
    assert again.status_code == 409 and "Not undone." in again.text


@pytest.mark.asyncio
async def test_undo_refuses_a_todo_someone_already_ticked(session, client):
    turn, _ = await _ask(session, ("add_todo", {"title": "Call the plumber"}))
    todo = session.exec(select(Todo)).one()
    todo.done = True
    session.add(todo); session.commit()
    [action] = actions.for_turn(session, turn.id)
    refused = client.post(f"/ask/actions/{action.id}/undo")
    assert refused.status_code == 409 and "already ticked off" in refused.text and "To-Dos" in refused.text
    session.expire_all()
    assert session.get(Todo, todo.id) is not None


@pytest.mark.asyncio
async def test_marking_a_bill_paid_waits_for_confirm_then_ticks_its_todo(session, client):
    doc = _bill(session)
    turn, gw = await _ask(session, ("propose_mark_bill_paid", {"document_id": doc.id}))
    [action] = actions.for_turn(session, turn.id)
    assert (action.tier, action.status) == ("confirm", "proposed")
    assert "Nothing has changed yet" in _tool_text(gw)
    assert [t.document_id for t, _ in _unpaid_bills(session, date(2027, 1, 1))] == [doc.id]     # nothing changed

    page = client.get(f"/ask/c/{turn.conversation_id}").text
    assert "Mark edp-2026-11.pdf as paid." in page and "EDP Comercial" in page and "€61.40" in page
    assert f"/ask/actions/{action.id}/confirm" in page and "Do it" in page

    done = client.post(f"/ask/actions/{action.id}/confirm", headers={"HX-Request": "true"})
    assert done.status_code == 200 and "marked paid" in done.text
    session.expire_all()
    assert _unpaid_bills(session, date(2027, 1, 1)) == []
    assert session.get(AskAction, action.id).status == "confirmed"
    assert client.post(f"/ask/actions/{action.id}/confirm").status_code == 409        # once only


@pytest.mark.asyncio
async def test_cancel_changes_nothing(session, client):
    doc = _bill(session)
    turn, _ = await _ask(session, ("propose_mark_bill_paid", {"document_id": doc.id}))
    [action] = actions.for_turn(session, turn.id)
    page = client.post(f"/ask/actions/{action.id}/cancel", headers={"HX-Request": "true"}).text
    assert "Cancelled" in page and "Nothing changed." in page
    session.expire_all()
    assert len(_unpaid_bills(session, date(2027, 1, 1))) == 1 and session.exec(select(Todo)).all() == []


@pytest.mark.asyncio
async def test_a_bill_outside_the_runs_reach_answers_like_a_missing_one(session):
    from tests import privacy_world as pw
    hidden = _bill(session)
    pw.set_levels(session, low=["house/house_appliance"])                   # bills have no level: withheld
    _, gw = await _ask(session, ("propose_mark_bill_paid", {"document_id": hidden.id}),
                       ("propose_mark_bill_paid", {"document_id": 99999}))
    assert _tool_text(gw, 0) == _tool_text(gw, 1)
    assert "edp-2026-11" not in json.dumps(gw.requests) and "EDP Comercial" not in json.dumps(gw.requests)
    assert session.exec(select(AskAction)).all() == []


@pytest.mark.asyncio
async def test_approving_the_inbox_lists_each_paper_and_says_which_need_a_detail(session, client, two_categories,
                                                                                inbox_documents_dir):
    from app.services import inbox_service
    from app.services.category_classifier import CategorySuggestion
    from app.services.ingestion import IncomingFile

    async def receive(name, suggestion):
        async def classify(file_path, context_text=None):
            return suggestion
        return await inbox_service.receive_document(
            session, IncomingFile(name, f"%PDF {name}".encode(), DocumentSource.EMAIL, "pedro@x"),
            context_text=None, external_ref=name, classify=classify)

    bill = await receive("vodafone.pdf", CategorySuggestion(Category.FINANCIALS, "bill", 0.95, "a bill"))
    manual = await receive("aeg-manual.pdf", CategorySuggestion(Category.HOUSE, "manual", 0.95, "a manual"))
    unsure = await receive("photo.pdf", CategorySuggestion(Category.HOUSE, "clip", 0.3, "?"))

    turn, gw = await _ask(session, ("propose_approve_inbox", {}))
    assert "2 Inbox paper(s)" in _tool_text(gw) and "vodafone" not in _tool_text(gw)   # a count, never the papers
    [action] = actions.for_turn(session, turn.id)
    page = client.get(f"/ask/c/{turn.conversation_id}").text
    assert "vodafone.pdf" in page and "aeg-manual.pdf" in page and "photo.pdf" not in page
    assert "Money › Bill" in page

    result = client.post(f"/ask/actions/{action.id}/confirm", headers={"HX-Request": "true"}).text
    assert "vodafone.pdf filed as Money › Bill." in result
    assert "aeg-manual.pdf was not filed." in result and f"/inbox/{manual.document.id}/card" in result
    session.expire_all()
    from app.models.document import Document
    statuses = {d.filename: d.status for d in session.exec(select(Document)).all()}
    assert statuses == {"vodafone.pdf": DocumentStatus.PROCESSED, "aeg-manual.pdf": DocumentStatus.PENDING_REVIEW,
                        "photo.pdf": DocumentStatus.PENDING_REVIEW}
    assert unsure.document.id not in actions.params(session.get(AskAction, action.id))["document_ids"]


@pytest.mark.asyncio
async def test_a_merchant_gets_its_category_as_the_merchants_page_does(payee_rules, client):
    session = payee_rules
    tasca = Merchant(canonical_name="Tasca do Zé", normalized_key="tasca do ze")
    session.add(tasca); session.commit()
    statement = make_document(session, category=Category.FINANCIALS, paper_kind="statement")
    session.add(Transaction(document_id=statement.id, description="TASCA DO ZE MAFRA", amount=23.5, currency="EUR", paid_date=date(2026, 11, 2),
                            transaction_type=TransactionType.DEBIT, merchant_id=tasca.id))
    session.commit()
    turn, gw = await _ask(session, ("propose_merchant_category", {"merchant": "tasca do ze",
                                                                  "category": "food.eat-out.restaurants"}),
                          ("propose_merchant_category", {"merchant": "Nobody", "category": "food.eat-out.restaurants"}))
    assert "is shown under the answer" in _tool_text(gw, 0) and "No merchant by that exact name" in _tool_text(gw, 1)
    [action] = actions.for_turn(session, turn.id)
    assert session.get(Merchant, tasca.id).confirmed is False                         # nothing until confirmed

    result = client.post(f"/ask/actions/{action.id}/confirm", headers={"HX-Request": "true"}).text
    assert "Tasca do Zé is filed under" in result and "1 unsorted line filed there" in result
    session.expire_all()
    node = get_node(session, "food.eat-out.restaurants")
    merchant = session.get(Merchant, tasca.id)
    assert merchant.confirmed and merchant.default_category_id == node.id
    assert session.exec(select(Transaction)).one().category_id == node.id
    rule = session.exec(select(Rule).where(Rule.merchant == "Tasca do Zé", Rule.node_id == node.id)).one()
    assert rule.source == PERSON and rule.enabled                                # the answer is a rule


def test_an_action_outside_an_answer_is_refused_before_it_changes_anything(session):
    with pytest.raises(RuntimeError):
        actions._add_todo(session, {"title": "x"})
    assert session.exec(select(Todo)).all() == []
