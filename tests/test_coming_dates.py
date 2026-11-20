"""PLAN step 10f: Coming up, Dates tab. The payments and bills it always showed plus the dated To-Dos of an area, one
area chip per row, chips to show one area; the month total stays the money due."""

from datetime import date, timedelta

from app.models.category import Category
from app.services.coming_dates import dates_view
from app.services.todo_backlog import add_todo
from app.services.taxonomy import ensure_taxonomy
from tests.test_coming_up import _bill, _commitment

TODAY = date.today()


def test_dates_add_the_dated_todos_without_changing_the_money(session):
    ensure_taxonomy(session)
    _bill(session, "EDP", 52.40, TODAY + timedelta(days=3))          # its own "Pay" to-do is the bill, not a second row
    _commitment(session, "Netflix", 13.99, next_due=TODAY + timedelta(days=5))
    add_todo(session, "Dentist: Rute", Category.HEALTH, TODAY + timedelta(days=4))
    add_todo(session, "Renew the domain", None, TODAY + timedelta(days=2))       # general: To-Dos page only
    add_todo(session, "Boiler service", Category.HOUSE, TODAY - timedelta(days=1))
    d = dates_view(session, TODAY)
    names = [r.name for m in d.months for r in m.rows]
    assert "Dentist: Rute" in names and "Renew the domain" not in names and names.count("EDP") == 1
    assert [r.name for r in d.overdue] == ["Boiler service"] and d.overdue[0].note == "To-Do, overdue"
    money_total = sum(m.total for m in d.view.months)
    assert round(sum(m.total for m in d.months), 2) == round(money_total, 2)
    assert [c[1] for c in d.chips][0] == "Money" and {c[0] for c in d.chips} == {"financials", "health", "house"}


def test_a_chip_shows_one_area_and_an_unknown_one_shows_all(client, session):
    _bill(session, "EDP", 52.40, TODAY + timedelta(days=3))
    add_todo(session, "Dentist: Rute", Category.HEALTH, TODAY + timedelta(days=4))
    only = dates_view(session, TODAY, "health")
    assert [r.name for m in only.months for r in m.rows] == ["Dentist: Rute"]
    assert len([r for m in dates_view(session, TODAY, "nope").months for r in m.rows]) == 2
    html = client.get("/financials/coming-up?area=health").text
    assert 'class="chip on" href="/financials/coming-up?area=health"' in html and "Dentist: Rute" in html
    assert ">EDP<" not in html
