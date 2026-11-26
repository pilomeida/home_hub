"""PLAN step 10c: Home. Ask first; Money this month (Earnings and Spending, three months each, past months filled to the
same day; available balance after what is still due this month); Coming up with To-Dos; Waiting for you as four slim
cards, the unconfirmed merchants on their own. Every number comes from the code that already feeds those pages."""

from datetime import date, timedelta

from sqlmodel import Session

from app.models.merchant import Merchant
from app.models.todo import Todo
from app.models.transaction import TransactionType
from app.services import home, review
from app.services.coming_up import coming_up
from tests.test_baseline import _cash_account
from tests.test_coming_up import _bill
from tests.test_overview_service import _doc, _txn
from tests.test_review import _doubtful

TODAY = date(2026, 11, 19)


def _flows(session):
    doc = _doc(session)
    for (y, m, d), amount in [((2026, 9, 5), 900.0), ((2026, 9, 25), 300.0), ((2026, 10, 3), 800.0),
                              ((2026, 10, 28), 400.0), ((2026, 11, 2), 1000.0)]:
        _txn(session, doc, "SHOP", amount, TransactionType.DEBIT, date(y, m, d))
        _txn(session, doc, "SALARY", amount * 2, TransactionType.CREDIT, date(y, m, d))
    _txn(session, doc, "TOMORROW", 5000.0, TransactionType.DEBIT, TODAY + timedelta(days=1))   # not happened yet
    _txn(session, doc, "OWN ACCOUNTS", 7000.0, TransactionType.TRANSFER, date(2026, 11, 3))    # neither earning nor spending
    return doc


def test_money_this_month_is_three_months_each_filled_to_the_same_day(session):
    _flows(session)
    earnings, spending = home.money_this_month(session, TODAY)
    assert [i.label for i in spending.items] == ["Sep", "Oct", "Nov"]
    sep, octo, nov = spending.items
    assert (sep.full, sep.value) == (1200.0, 900.0)              # the 25th is after the 19th
    assert (octo.full, octo.value) == (1200.0, 800.0)
    assert nov.current and nov.full is None and nov.value == 1000.0 == spending.now   # tomorrow's 5,000 not drawn
    assert spending.delta_pct == 25.0 and spending.delta_good is False and spending.last_label == "Oct"
    assert earnings.now == 2000.0 and earnings.delta_pct == 25.0 and earnings.delta_good is True


def test_available_is_the_bank_balance_minus_what_is_still_due_this_month(session):
    _cash_account(session)                                       # the bank said €404.71, no line since
    _bill(session, "EDP", 52.40, date(2026, 11, 25))             # due this month
    _bill(session, "Vodafone", 42.00, date(2026, 12, 5))         # next month: not taken off
    _bill(session, "Water", 18.10, date(2026, 11, 10))           # overdue, its to-do still open: still to pay
    a = home.available(session, TODAY)
    view = coming_up(session, TODAY)
    assert a.balance == 404.71 and a.still_due_count == 2 and a.still_due == 70.50
    assert a.after == 334.21
    assert {i.name for i in view.overdue} == {"Water"}


def test_no_balance_until_a_bank_is_read(session):
    assert home.available(session, TODAY) is None


def test_coming_up_lists_bills_and_to_dos_once_each_by_date(session):
    _bill(session, "EDP", 52.40, date(2026, 11, 25))             # its "Pay EDP" to-do is the bill itself
    session.add(Todo(title="Renew Rute's passport", due_date=date(2026, 11, 22)))
    session.add(Todo(title="No date yet"))
    session.commit()
    lines = home.coming_lines(session, TODAY)
    assert [(l.name, l.tag) for l in lines] == [("Renew Rute's passport", "To-Do"), ("EDP", "Bill")]


def test_merchants_are_their_own_card_and_line_and_leave_the_menu_count(session, levels_not_binding):
    _doubtful(session)                                           # one AI answer waits (Money)
    for n in ("Loja A", "Loja B"):
        session.add(Merchant(canonical_name=n, normalized_key=n.lower()))
    session.commit()
    review.sync(session)
    cards = {c.label: c.count for c in home.waiting_cards(session)}
    assert cards == {"Inbox": 0, "Money": 1, "Health": 0, "Merchants": 2}
    assert review.count(session, merchants=False) == 1 and review.merchant_count(session) == 2
    assert review.count(session) == 3                            # the queue itself still holds all of them
    data = home.home_data(session, TODAY)
    assert data.waiting_total == 1                               # the heading counts as the menu does


def test_the_menu_and_the_queue_page_show_merchants_apart(client, engine, levels_not_binding, monkeypatch):
    monkeypatch.setattr("app.db.engine", engine)                 # the menu count opens its own session
    with Session(engine) as session:
        _doubtful(session)
        session.add(Merchant(canonical_name="Loja A", normalized_key="loja a"))
        session.commit()
    html = client.get("/waiting").text
    assert 'Waiting for you <em>1</em>' in html                  # the menu leaves the merchant out
    assert "1 new merchant not confirmed yet" in html and html.count('class="card witem"') == 1
    only = client.get("/waiting?area=merchants").text
    assert "<td>Loja A</td>" in only and "no guess: choose one" in only   # merchants are a table since step 10e
    assert only.count('class="card witem"') == 0


def test_home_page_has_ask_first_money_coming_and_waiting(client, session):
    _flows(session)
    html = client.get("/").text
    assert html.index('action="/ask"') < html.index("Money this month") < html.index("Waiting for you ·")
    assert 'name="question"' in html and "Coming up" in html and 'href="/overview"' in html
    assert html.count('class="chart"') == 2                      # Earnings and Spending
    assert "budget" not in html.lower()
    assert "No balance yet" in html
    for label in ("Inbox", "Money", "Health", "Merchants"):
        assert f'<div class="wi-a">{label}</div>' in html


def test_the_overview_moved_to_its_own_page(client, session):
    _flows(session)
    html = client.get("/overview").text
    assert "fc-page" in html and html.count('class="chart"') >= 3
    assert 'class="pill on" href="/"' in html                    # still under Home in the menu


def test_home_has_one_ask_box_and_other_pages_keep_the_bar_search(client, session):
    """PLAN step 13: Home asks in its large box only; the small bar search stays on the other pages."""
    home = client.get("/").text
    assert home.count('role="search"') == 0 and 'placeholder="Ask the Hub"' in home
    assert 'class="nav-search"' in client.get("/papers").text
