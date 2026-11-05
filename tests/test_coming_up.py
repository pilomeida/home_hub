"""Work-list B3b (Pedro, 2026-11-04): a Coming up page under Money lists the next three months: the commitments a
person sets up and the bills filed but not paid, each payment once; budgets are separate."""

from datetime import date, timedelta

import pytest
from sqlmodel import select

from app.models.budget import Budget
from app.models.commitment import Cadence, Commitment
from app.models.category import Category
from app.models.document import DocumentStatus
from app.models.todo import Todo
from app.models.transaction import Transaction, TransactionType
from app.services import coming_up as svc
from app.services.taxonomy import ensure_taxonomy, get_node
from app.services.todo_engine import generate_todo_for_transaction
from tests.knowledge_factories import make_document

HTML = {"Accept": "text/html"}
TODAY = date.today()


@pytest.fixture(autouse=True)
def _tree(session):
    ensure_taxonomy(session)


def _leaf(session, slug="housing.utilities.streaming"):
    return get_node(session, slug)


def _bill(session, name, amount, due, leaf=None, todo=True):
    doc = make_document(session, category=Category.FINANCIALS, paper_kind="bill", filename=f"{name}.pdf")
    t = Transaction(document_id=doc.id, description=name, transaction_type=TransactionType.DEBIT, amount=amount,
                    currency="EUR", due_date=due, issue_date=due - timedelta(days=20),
                    category_id=leaf.id if leaf else None)
    session.add(t); session.commit(); session.refresh(t)
    if todo:
        generate_todo_for_transaction(session, t); session.commit()      # the bill's own "Pay ..." to-do
    return doc, t


def _commitment(session, name="Netflix", amount=13.99, cadence=Cadence.MONTHLY, next_due=None, leaf=None):
    return svc.create_commitment(session, name=name, amount=str(amount), cadence=cadence.value,
                                 next_due=(next_due or TODAY + timedelta(days=5)).isoformat(),
                                 category_id=str((leaf or _leaf(session)).id))


def test_months_roll_forward_and_keep_the_end_of_the_month():
    assert svc.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    c = Commitment(name="x", cadence=Cadence.MONTHLY, planned_amount=1, next_due_date=date(2026, 9, 10))
    assert svc.occurrences(c, date(2026, 11, 5), date(2027, 2, 5)) == [date(2026, 11, 10), date(2026, 12, 10),
                                                                      date(2027, 1, 10)]
    yearly = Commitment(name="IMI", cadence=Cadence.YEARLY, planned_amount=300, next_due_date=date(2026, 5, 31))
    assert svc.occurrences(yearly, date(2026, 11, 5), date(2027, 6, 5)) == [date(2027, 5, 31)]
    once = Commitment(name="Boiler", cadence=Cadence.IRREGULAR, planned_amount=90, next_due_date=date(2026, 12, 1))
    assert svc.occurrences(once, date(2026, 11, 5), date(2027, 2, 5)) == [date(2026, 12, 1)]


def test_a_commitment_added_by_hand_shows_on_its_date_and_no_budget_changes(client, session):
    leaf = _leaf(session)
    due = TODAY + timedelta(days=9)
    r = client.post("/financials/coming-up/commitments", data={
        "name": "Netflix", "amount": "13,99", "cadence": "monthly", "next_due": due.isoformat(),
        "category_id": str(leaf.id)}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/financials/coming-up", headers=HTML).text
    assert "Netflix" in page and due.isoformat() in page and "commitment (monthly)" in page and "€13.99" in page
    assert "Housing › Utilities › Streaming" in page
    assert session.exec(select(Budget)).all() == []                       # separate from the budget


def test_unpaid_bills_show_once_paid_or_settled_ones_do_not(session):
    _bill(session, "Fidelidade", 123.11, TODAY + timedelta(days=3))
    _paid_doc, paid = _bill(session, "Vodafone", 9.98, TODAY + timedelta(days=10))
    todo = session.exec(select(Todo).where(Todo.transaction_id == paid.id)).one()
    todo.done = True; session.add(todo); session.commit()                 # a person said it is paid
    _bill(session, "Old", 20.0, TODAY - timedelta(days=4))                # past due, its to-do still open
    view = svc.coming_up(session, TODAY)
    names = [i.name for m in view.months for i in m.items]
    assert names == ["Fidelidade"]
    assert [(i.name, i.note) for i in view.overdue] == [("Old", "overdue: not paid yet")]


def test_a_bill_for_a_commitment_is_counted_once(session):
    leaf = _leaf(session, "housing.utilities.electricity")
    _commitment(session, name="Electricity", amount=70, next_due=TODAY + timedelta(days=12), leaf=leaf)
    _bill(session, "EDP", 74.30, TODAY + timedelta(days=15), leaf=leaf)
    items = [i for m in svc.coming_up(session, TODAY).months for i in m.items]
    first_month = [i for i in items if abs((i.when - (TODAY + timedelta(days=13))).days) <= 3]
    assert [(i.name, i.kind) for i in first_month] == [("EDP", "bill")]   # not EDP and Electricity
    assert any(i.name == "Electricity" for i in items if i.when > TODAY + timedelta(days=25))   # its later dates stay


def test_a_commitment_with_parts_missing_is_refused_in_three_parts(client, session):
    r = client.post("/financials/coming-up/commitments", data={"name": "", "amount": "abc", "cadence": "monthly",
                                                              "next_due": "", "category_id": ""}, headers=HTML)
    assert r.status_code == 400
    assert "Not saved." in r.text and "A commitment needs a name, an amount above zero" in r.text
    assert "Fill those in, then save again." in r.text and session.exec(select(Commitment)).all() == []


def test_a_commitment_linked_to_bank_lines_is_not_removed(client, session):
    c = _commitment(session)
    cid = c.id
    _doc, line = _bill(session, "Netflix", 13.99, TODAY - timedelta(days=30), todo=False)
    line.commitment_id = c.id; session.add(line); session.commit()
    r = client.post(f"/financials/coming-up/commitments/{c.id}/remove", headers=HTML, follow_redirects=False)
    assert r.status_code == 409 and "Not removed." in r.text and "1 bank line(s)" in r.text
    line.commitment_id = None; session.add(line); session.commit()
    assert client.post(f"/financials/coming-up/commitments/{c.id}/remove", follow_redirects=False).status_code == 303
    session.expire_all()
    assert session.exec(select(Commitment).where(Commitment.id == cid)).first() is None


def test_this_repeats_on_a_bank_line_fills_in_the_form(client, session):
    leaf = _leaf(session)
    doc = make_document(session, category=Category.FINANCIALS, paper_kind="statement", status=DocumentStatus.PROCESSED)
    line = Transaction(document_id=doc.id, description="NETFLIX.COM", transaction_type=TransactionType.DEBIT,
                       amount=13.99, currency="EUR", paid_date=date(2026, 10, 14), category_id=leaf.id)
    session.add(line); session.commit(); session.refresh(line)
    page = client.get(f"/financials/coming-up?from_line={line.id}", headers=HTML).text
    assert 'value="NETFLIX.COM"' in page and 'value="13.99"' in page and 'value="2026-11-14"' in page
    assert f'<option value="{leaf.id}" selected>' in page


def test_the_overview_says_what_is_coming_in_the_next_30_days(client, session):
    _bill(session, "Fidelidade", 123.11, TODAY + timedelta(days=3))
    page = client.get("/", headers=HTML).text
    assert "Coming up in the next 30 days: 1 payment, €123.11" in page and 'href="/financials/coming-up"' in page
