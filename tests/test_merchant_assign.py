import pytest

from app.models.merchant import Merchant
from app.models.transaction import TransactionType
from app.services.merchant_assign import assign_category, list_merchants
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy, get_node
from tests.test_merchant_reconcile import _merchant, _t

SM, FUEL, REST = "food.groceries.supermarket", "transport.car-running-costs.fuel", "food.eat-out.restaurants"


def _node(session, slug):
    return get_node(session, slug)


def test_assign_sets_default_confirms_and_files_unsorted_rows(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Shop")
    a = _t(session, m, UNSORTED_SLUG)
    report = assign_category(session, [m.id], _node(session, SM), refile_existing=False)
    session.refresh(a); session.refresh(m)
    assert a.category_id == _node(session, SM).id
    assert m.default_category_id == _node(session, SM).id and m.confirmed is True
    assert (report.merchants, report.filed_from_unsorted, report.moved_from_elsewhere) == (1, 1, 0)


def test_changing_a_category_leaves_old_entries_unless_refile_is_asked(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Shop", REST)
    old = _t(session, m, REST)
    assign_category(session, [m.id], _node(session, SM), refile_existing=False)
    session.refresh(old)
    assert old.category_id == _node(session, REST).id  # past entry stays
    report = assign_category(session, [m.id], _node(session, SM), refile_existing=True)
    session.refresh(old)
    assert old.category_id == _node(session, SM).id and report.moved_from_elsewhere == 1


def test_dry_run_counts_without_writing(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Shop", REST)
    old = _t(session, m, REST)
    report = assign_category(session, [m.id], _node(session, SM), refile_existing=True, dry_run=True)
    session.refresh(old); session.refresh(m)
    assert report.moved_from_elsewhere == 1
    assert old.category_id == _node(session, REST).id and m.default_category_id == _node(session, REST).id


def test_loan_linked_rows_and_transfers_are_never_moved_and_credits_follow_the_refund_rule(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Bank")
    loan = _t(session, m, "loans-debt.loan-repayments.mortgage"); loan.debt_id = 1; session.add(loan)
    transfer = _t(session, m, UNSORTED_SLUG, TransactionType.TRANSFER)
    purchase = _t(session, m, UNSORTED_SLUG)
    refund = _t(session, m, UNSORTED_SLUG, TransactionType.CREDIT)
    session.commit()
    assign_category(session, [m.id], _node(session, SM), refile_existing=True)
    for t in (loan, transfer, purchase, refund):
        session.refresh(t)
    assert loan.category_id == _node(session, "loans-debt.loan-repayments.mortgage").id
    assert transfer.category_id == _node(session, UNSORTED_SLUG).id
    assert purchase.category_id == refund.category_id == _node(session, SM).id


def test_credit_only_merchant_cannot_become_a_refund_but_can_take_an_income_node(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Client")
    c = _t(session, m, UNSORTED_SLUG, TransactionType.CREDIT)
    assign_category(session, [m.id], _node(session, SM), refile_existing=True)
    session.refresh(c)
    assert c.category_id == _node(session, UNSORTED_SLUG).id
    assign_category(session, [m.id], _node(session, "income.psi.sessions"), refile_existing=True)
    session.refresh(c)
    assert c.category_id == _node(session, "income.psi.sessions").id


def test_only_a_real_leaf_is_accepted(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Shop")
    with pytest.raises(ValueError):
        assign_category(session, [m.id], _node(session, "food"))
    with pytest.raises(ValueError):
        assign_category(session, [m.id], _node(session, UNSORTED_SLUG))


def test_many_merchants_in_one_go(session):
    ensure_taxonomy(session)
    ms = [_merchant(session, f"Shop {i}") for i in range(3)]
    rows = [_t(session, m, UNSORTED_SLUG) for m in ms]
    report = assign_category(session, [m.id for m in ms], _node(session, SM))
    assert report.merchants == 3 and report.filed_from_unsorted == 3
    for t in rows:
        session.refresh(t)
        assert t.category_id == _node(session, SM).id


def test_list_merchants_filters_search_and_scope(session):
    ensure_taxonomy(session)
    a = _merchant(session, "Padaria Sol")
    b = _merchant(session, "Galp", FUEL, confirmed=True)
    _t(session, a, UNSORTED_SLUG); _t(session, b, FUEL)
    everything = list_merchants(session)
    assert {r.name for r in everything.rows} == {"Padaria Sol", "Galp"}
    assert [r.name for r in list_merchants(session, q="padaria").rows] == ["Padaria Sol"]
    assert [r.name for r in list_merchants(session, scope="undecided").rows] == ["Padaria Sol"]
    galp = next(r for r in everything.rows if r.name == "Galp")
    assert galp.category_label == "Transport › Car running costs › Fuel" and galp.entries == 1 and galp.elsewhere == 0


# --- the page ---------------------------------------------------------------

def _setup_page(session):
    ensure_taxonomy(session)
    m1, m2 = _merchant(session, "Padaria Sol"), _merchant(session, "Mercearia Lua", REST)
    _t(session, m1, UNSORTED_SLUG); old = _t(session, m2, REST)
    return m1, m2, old


def test_bulk_page_lists_merchants_with_the_category_picker(client, session):
    _setup_page(session)
    page = client.get("/financials/transactions/bulk", params={"scope": "all"})
    assert page.status_code == 200
    assert "Padaria Sol" in page.text and "Mercearia Lua" in page.text
    assert 'value="food.groceries.supermarket"' in page.text and "unsorted.needs-review" not in page.text


def test_bulk_preview_changes_nothing_and_apply_does(client, session):
    from app.models.transaction import Transaction
    m1, m2, old = _setup_page(session)
    data = {"merchant_ids": [str(m1.id), str(m2.id)], "category_node": SM, "refile_existing": "1", "step": "preview"}
    preview = client.post("/financials/transactions/bulk", data=data)
    assert preview.status_code == 200 and "Review before applying" in preview.text
    assert "filed under another category will move" in preview.text
    session.expire_all()
    assert session.get(Transaction, old.id).category_id == _node(session, REST).id  # preview wrote nothing

    done = client.post("/financials/transactions/bulk", data={**data, "step": "apply"}, follow_redirects=False)
    assert done.status_code == 303 and "applied=2" in done.headers["location"] and "moved=1" in done.headers["location"]
    session.expire_all()
    assert session.get(Transaction, old.id).category_id == _node(session, SM).id
    assert session.get(Merchant, m1.id).default_category_id == _node(session, SM).id


def test_bulk_apply_needs_merchants_and_a_real_category(client, session):
    m1, _, _ = _setup_page(session)
    assert client.post("/financials/transactions/bulk", data={"category_node": SM, "step": "apply"}).status_code == 400
    assert client.post("/financials/transactions/bulk",
                       data={"merchant_ids": [str(m1.id)], "category_node": "food", "step": "apply"}).status_code == 400


def test_confirming_one_merchant_can_move_its_earlier_entries(client, session):
    from app.models.transaction import Transaction
    ensure_taxonomy(session)
    m = _merchant(session, "Shop", REST)
    old = _t(session, m, REST)
    client.post(f"/financials/transactions/merchants/{m.id}/confirm", data={"category_node": SM})
    session.expire_all()
    assert session.get(Transaction, old.id).category_id == _node(session, REST).id  # default: untouched
    client.post(f"/financials/transactions/merchants/{m.id}/confirm", data={"category_node": SM, "refile_existing": "1"})
    session.expire_all()
    assert session.get(Transaction, old.id).category_id == _node(session, SM).id
