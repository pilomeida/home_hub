from datetime import date

import pytest
from sqlmodel import select

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.merchant import Merchant
from app.models.todo import Todo
from app.models.transaction import Category, Transaction, TransactionType
from app.services.document_reconcile import reconcile_documents

_n = [0]


def _doc(session, category, source=DocumentSource.MANUAL, name=None):
    _n[0] += 1
    d = Document(filename=name or f"d{_n[0]}.pdf", file_path=f"/tmp/d{_n[0]}.pdf", content_hash=f"rec-{_n[0]}",
                 source=source, category=category, status=DocumentStatus.PROCESSED)
    session.add(d); session.commit(); session.refresh(d)
    return d


def _txn(session, doc, provider, amount, paid, ttype=TransactionType.DEBIT, **kw):
    t = Transaction(document_id=doc.id, provider=provider, category=Category.OTHER, transaction_type=ttype,
                    amount=amount, paid_date=paid, **kw)
    session.add(t); session.commit(); session.refresh(t)
    return t


@pytest.fixture()
def bank(session):
    return _doc(session, "statement", name="statement.pdf")


@pytest.fixture()
def bill(session):
    return _doc(session, "bill", name="bill.pdf")


def test_a_bill_is_settled_by_the_later_bank_debit_of_the_same_amount_and_payee(session, bank, bill):
    b = _txn(session, bill, "Coopérnico", 172.55, date(2024, 11, 25))
    d = _txn(session, bank, "DÉBITO DIRETO-COOPERNICO-COOPERN", 172.55, date(2024, 12, 18))
    report = reconcile_documents(session)
    session.refresh(b)
    assert b.settled_by_id == d.id and report.settled == 1


def test_it_needs_the_same_amount_a_payee_in_common_and_a_sane_date_window(session, bank, bill):
    b = _txn(session, bill, "Coopérnico", 172.55, date(2024, 11, 25))
    _txn(session, bank, "DÉBITO DIRETO-COOPERNICO", 172.50, date(2024, 12, 18))      # other amount
    _txn(session, bank, "CONTINENTE", 172.55, date(2024, 12, 18))                      # other payee
    _txn(session, bank, "DÉBITO DIRETO-COOPERNICO", 172.55, date(2025, 3, 1))          # too late
    _txn(session, bank, "DÉBITO DIRETO-COOPERNICO", 172.55, date(2024, 10, 1))         # long before
    reconcile_documents(session)
    session.refresh(b)
    assert b.settled_by_id is None


def test_the_bank_may_come_a_few_days_before_the_document(session, bank):
    receipt = _doc(session, None, name="metlife.pdf")
    r = _txn(session, receipt, "MetLife", 109.64, date(2026, 8, 14))
    d = _txn(session, bank, "DEBITO DIRETO-METLIFE EUROPE D-00154572953", 109.64, date(2026, 8, 11))
    _txn(session, bank, "DEBITO DIRETO-METLIFE EUROPE D-00154572953", 109.64, date(2026, 9, 2))
    reconcile_documents(session)
    session.refresh(r)
    assert r.settled_by_id == d.id  # the nearest one, not the September debit


def test_one_bank_row_settles_at_most_one_document(session, bank, bill):
    a = _txn(session, bill, "Coopérnico", 80.0, date(2025, 5, 25))
    other = _doc(session, "bill")
    b = _txn(session, other, "Coopérnico", 80.0, date(2025, 6, 25))
    d1 = _txn(session, bank, "DÉBITO DIRETO-COOPERNICO", 80.0, date(2025, 6, 20))
    d2 = _txn(session, bank, "DÉBITO DIRETO-COOPERNICO", 80.0, date(2025, 7, 22))
    reconcile_documents(session)
    session.refresh(a); session.refresh(b)
    assert (a.settled_by_id, b.settled_by_id) == (d1.id, d2.id)


def test_bank_rows_from_the_api_count_as_bank_and_statement_rows_are_never_settled(session, bill):
    api = _doc(session, None, source=DocumentSource.API, name="sync")
    b = _txn(session, bill, "Vodafone", 25.0, date(2026, 3, 1))
    d = _txn(session, api, "VODAFONE PORTUGAL", 25.0, date(2026, 3, 9), external_id="X1")
    reconcile_documents(session)
    session.refresh(b); session.refresh(d)
    assert b.settled_by_id == d.id and d.settled_by_id is None


def test_the_merchant_name_also_identifies_the_payee(session, bank, bill):
    m = Merchant(canonical_name="Galp Energia", normalized_key="galp energia")
    session.add(m); session.commit()
    b = _txn(session, bill, "Fatura 123", 40.0, date(2026, 1, 5), merchant_id=m.id)
    d = _txn(session, bank, "DEBITO DIRETO-GALP-998", 40.0, date(2026, 1, 20), merchant_id=m.id)
    reconcile_documents(session)
    session.refresh(b)
    assert b.settled_by_id == d.id


def test_settling_closes_the_open_todo_and_carries_the_commitment_to_the_bank_row(session, bank, bill):
    from app.models.commitment import Cadence, Commitment
    commitment = Commitment(name="Electricity", cadence=Cadence.MONTHLY, planned_amount=70.0)
    session.add(commitment); session.commit()
    b = _txn(session, bill, "Coopérnico", 66.3, date(2026, 5, 25), commitment_id=commitment.id)
    d = _txn(session, bank, "DÉBITO DIRETO-COOPERNICO", 66.3, date(2026, 6, 30))
    todo = Todo(title="Pay Coopérnico", transaction_id=b.id, due_date=date(2026, 6, 25)); session.add(todo); session.commit()
    reconcile_documents(session)
    session.refresh(todo); session.refresh(d)
    assert todo.done is True and d.commitment_id == commitment.id


def test_dry_run_reports_without_writing_and_it_is_idempotent(session, bank, bill):
    b = _txn(session, bill, "Coopérnico", 63.48, date(2025, 7, 25))
    _txn(session, bank, "DÉBITO DIRETO-COOPERNICO", 63.48, date(2025, 9, 4))
    dry = reconcile_documents(session, dry_run=True)
    session.refresh(b)
    assert dry.settled == 1 and b.settled_by_id is None
    assert reconcile_documents(session).settled == 1
    assert reconcile_documents(session).settled == 0


def test_an_unmatched_bill_stays_counted_and_is_listed_with_the_closest_same_payee_debit(session, bank, bill):
    b = _txn(session, bill, "Coopérnico", 60.40, date(2025, 6, 25))
    near = _txn(session, bank, "DÉBITO DIRETO-COOPERNICO", 60.0, date(2025, 7, 20))
    report = reconcile_documents(session)
    session.refresh(b)
    assert b.settled_by_id is None and report.unmatched[0].transaction_id == b.id
    assert report.unmatched[0].nearest == (near.id, 60.0, date(2025, 7, 20))


# --- a settled row counts nowhere ----------------------------------------------------------------

def _settled_pair(session, bank, bill, amount=120.0, provider_bill="Coopérnico", provider_bank="DÉBITO DIRETO-COOPERNICO"):
    from app.services.taxonomy import ensure_taxonomy, file_transaction, get_node
    ensure_taxonomy(session)
    node = get_node(session, "housing.utilities.electricity")
    paid = date.today().replace(day=1)
    b = _txn(session, bill, provider_bill, amount, paid)
    d = _txn(session, bank, provider_bank, amount, paid)
    for t in (b, d):
        file_transaction(session, t, node)
    session.commit()
    assert reconcile_documents(session).settled == 1
    session.refresh(b); session.refresh(d)
    return b, d, node


def test_budget_and_overview_count_the_bank_row_once(session, bank, bill):
    from app.services.budget_service import _load_buckets
    from app.services.overview_service import _monthly_flow_totals
    from sqlmodel import select as sel
    from app.models.category_node import CategoryNode
    b, d, node = _settled_pair(session, bank, bill)
    nodes = {n.id: n for n in session.exec(sel(CategoryNode)).all()}
    by_node, _, _ = _load_buckets(session, date.today(), nodes)
    assert sum(by_node[node.id].values()) == 120.0  # not 240
    _income, expense = _monthly_flow_totals(session, date.today())
    assert sum(expense.values()) == 120.0


def test_tags_ask_and_review_queues_ignore_the_settled_row(session, bank, bill):
    from app.domains.financials.ask import _spending
    from app.services.classification_engine import unsorted_transactions_query
    from app.services.tag_service import ensure_tags, tag_totals
    b, d, node = _settled_pair(session, bank, bill, amount=75.0)
    ensure_tags(session)
    total = next(t for t in tag_totals(session, date.today()) if t.name == "house")
    assert total.month_to_date == 75.0
    first = date.today().replace(day=1).isoformat()
    text = _spending(session, {"date_from": first, "date_to": date.today().isoformat()}).text
    assert "75.00" in text and "150.00" not in text
    b.category_id = None; d.category_id = None; session.add(b); session.add(d); session.commit()
    assert [t.id for t in session.exec(unsorted_transactions_query()).all()] == [d.id]


@pytest.mark.asyncio
async def test_withdrawing_the_bank_document_reopens_the_bill(session, bank, bill):
    from app.domains.financials.handler import FinancialsHandler
    b, d, _node = _settled_pair(session, bank, bill)
    await FinancialsHandler().withdraw(session, bank)
    session.refresh(b)
    assert b.settled_by_id is None and session.get(Transaction, d.id) is None


def test_the_pages_show_the_document_on_the_bank_entry_and_the_settled_row_as_such(client, session, bank, bill):
    b, d, _node = _settled_pair(session, bank, bill)
    page = client.get("/financials/transactions").text
    assert "settled by bank entry" in page and "tr class=\"flow-settled\"" in page
    assert f"/financials/bills/{bill.id}" in page and "&#128206; document" in page
    detail = client.get(f"/financials/bills/{bill.id}").text
    assert "see the bank entry" in detail and f"transaction_id={d.id}" in detail
    unmatched = _txn(session, _doc(session, "bill"), "EDP", 10.0, date(2026, 1, 1))
    assert "not matched to a bank entry yet" in client.get(f"/financials/bills/{unmatched.document_id}").text
