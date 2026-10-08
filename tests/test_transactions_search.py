from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy
from tests.test_merchant_reconcile import _merchant, _t


def _data(session):
    ensure_taxonomy(session)
    a, b = _merchant(session, "Padaria Sol"), _merchant(session, "Galp")
    for i in range(105):  # more than one page of 100
        _t(session, a if i % 2 else b, UNSORTED_SLUG, provider=f"COMPRA {i} PADARIA")
    return a, b


def test_next_page_works_when_the_form_sends_blank_filters(client, session):
    _data(session)
    blank = ("category=&category_node=&tag=&nature=&account_id=&date_from=&date_to=&commitment_id=&debt_id="
             "&transaction_type=&merchant_id=&page=2")
    assert client.get(f"/financials/transactions?{blank}").status_code == 200  # was a 422
    assert client.get("/financials/transactions?page=abc&account_id=zz").status_code == 200


def test_pager_links_carry_only_the_filters_that_are_set(client, session):
    _data(session)
    page = client.get("/financials/transactions", params={"q": "padaria"})
    assert "page=2" in page.text and "account_id=&" not in page.text and "q=padaria" in page.text


def test_search_matches_merchant_name_and_provider_text(client, session):
    _data(session)
    by_merchant = client.get("/financials/transactions", params={"q": "galp"}).text
    assert "Galp" in by_merchant and "Padaria Sol" not in by_merchant
    by_provider = client.get("/financials/transactions", params={"q": "COMPRA 7 "}).text
    assert "COMPRA 7 PADARIA" in by_provider and "COMPRA 8 PADARIA" not in by_provider
