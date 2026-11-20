"""PLAN step 10d: Money is one page (headline row, the breakdown and its trend; tabs Spending, Bills & due, Loans,
Utilities, All lines) and a category history: any category at any level, a number of months, four views."""

import re
from datetime import date, timedelta

from app.models.merchant import Merchant
from app.models.transaction import TransactionType
from app.services import money_page as mp
from app.services.overview_service import get_category_comparison
from app.services.taxonomy import ensure_taxonomy, get_node
from app.ui.charts import MAX_EXAGGERATION, exaggeration, make_scale
from tests.test_baseline import _cash_account
from tests.test_coming_up import _bill
from tests.test_overview_service import _doc, _txn

TODAY = date(2026, 11, 19)
SUPER = "food.groceries.supermarket"


def _groceries(session):
    ensure_taxonomy(session)
    doc = _doc(session)
    shop = Merchant(canonical_name="Continente", normalized_key="continente")
    lidl = Merchant(canonical_name="Lidl", normalized_key="lidl")
    session.add(shop); session.add(lidl); session.commit()
    for (y, m, d), amount, who in [((2026, 9, 5), 300.0, shop), ((2026, 9, 25), 120.0, lidl), ((2026, 10, 3), 260.0, lidl),
                                   ((2026, 10, 4), 90.0, shop), ((2026, 10, 28), 150.0, shop), ((2026, 11, 2), 210.0, shop)]:
        t = _txn(session, doc, who.canonical_name.upper(), amount, TransactionType.DEBIT, date(y, m, d), leaf=SUPER)
        t.merchant_id = who.id; session.add(t)
    _txn(session, doc, "NOT FILED", 40.0, TransactionType.DEBIT, date(2026, 11, 5))
    _txn(session, doc, "TOMORROW", 999.0, TransactionType.DEBIT, TODAY + timedelta(days=1), leaf=SUPER)
    session.commit()
    return doc


def test_history_of_a_level_one_category_adds_everything_under_it(session):
    _groceries(session)
    for slug in ("food", "food.groceries", SUPER):               # any level gives the same months here
        h = mp.category_history(session, slug, 3, TODAY)
        assert [(m.key, m.spent, m.by_same_day) for m in h.months] == [
            ("2026-09", 420.0, 300.0), ("2026-10", 500.0, 350.0), ("2026-11", 210.0, 210.0)]
    assert h.months[-1].current and h.months[0].top_merchant == "Continente" and h.months[1].top_merchant == "Lidl"
    assert h.average == 460.0 and h.highest.key == "2026-10"
    assert "category_node=food.groceries.supermarket&date_from=2026-10-01&date_to=2026-10-31" in h.months[1].lines_url
    assert h.months[-1].lines_url.endswith("date_to=2026-11-19")


def test_history_agrees_with_the_overview_breakdown(session):
    _groceries(session)
    food = next(r for r in get_category_comparison(session, TODAY) if r.category == "Food")
    h = mp.category_history(session, "food", 3, TODAY)
    assert food.current_value == h.months[-1].spent and food.last_month_value == h.months[1].spent


def test_lines_not_filed_yet_have_their_own_history(session):
    _groceries(session)
    h = mp.category_history(session, mp.UNSORTED, 3, TODAY)
    assert h.months[-1].spent == 40.0 and h.label == "Not filed yet"


def test_an_unknown_category_is_refused_in_three_parts(client, session):
    ensure_taxonomy(session)
    assert mp.category_history(session, "nope", 12, TODAY) is None
    r = client.get("/money/history?category=nope", headers={"accept": "text/html"})
    assert r.status_code == 404 and "There is no spending category with that name." in r.text


def test_every_view_follows_the_chart_rule(client, session):
    _groceries(session)
    h = mp.category_history(session, "food", 12, TODAY)
    values = [v for i in h.items for v in (i.full, i.value) if v]
    assert exaggeration(make_scale(values), values) <= MAX_EXAGGERATION + 1e-9
    for view in mp.VIEWS:
        html = client.get(f"/money/history?category=food&months=12&view={view}").text
        svg = " ".join(re.findall(r"<svg.*?</svg>", html, re.S))
        assert 'class="chart"' in html and "€999" not in svg and "budget" not in svg.lower(), view
    page = client.get("/money/history?category=food&months=6").text
    assert '<option value="food.groceries.supermarket"' in page and "Food › Groceries" in page
    assert page.count("Lines ›") == 6


def test_the_money_page_has_the_headline_the_breakdown_and_the_tabs(client, session):
    _groceries(session)
    _cash_account(session)
    _bill(session, "EDP", 52.40, date.today() + timedelta(days=3))
    html = client.get("/money").text
    for label in ("Spent this month", "Cash balance", "Due soon", "Loans"):
        assert f'<div class="sub">{label}</div>' in html
    assert "€404.71" in html and "€52.40" in html and "1 in the next 7 days" in html and "0 open" in html
    assert html.count('class="chart"') == 2                      # the breakdown and the 12 months
    tabs = re.findall(r'<nav class="sectionbar".*?</nav>', html, re.S)[0]
    assert [t for t in re.findall(r'>([^<]+)</a>', tabs)] == ["Spending", "Bills &amp; due", "Loans", "Utilities",
                                                              "All lines", "Category history"]
    assert 'class="pill on" href="/money"' in html


def test_old_money_pages_stay_in_the_money_section(client):
    html = client.get("/financials/bills").text
    assert 'class="pill on" href="/money"' in html and 'href="/money/history"' in html
