"""PLAN step 10b: the shell. The seven-item menu, the header, the gear, the phone bar; every page the old side menu
reached is still reachable, and old URLs keep working."""

import re

from app.categories.registry import implemented_categories
from app.ui import shell

# Everything the side menu linked to before step 10b (base.html, 2026-11-19).
OLD_MENU = ["/", "/ask", "/inbox", "/waiting", "/wiki", "/todos", "/privacy", "/schedules", "/problems",
            "https://recipes.cdafamily.casa", "https://gym.cdafamily.casa"]


def test_every_page_the_old_menu_reached_is_still_reachable():
    reachable = {l.url for l in shell.every_link()}
    old = OLD_MENU + [l.url for spec in implemented_categories() for l in spec.nav_links]
    assert [u for u in old if u not in reachable] == []


def test_the_menu_has_the_seven_items_in_order():
    assert [s.label for s in shell.sections()] == ["Home", "Money", "Health", "Things", "Papers", "Coming up",
                                                   "Waiting for you"]


def test_a_page_belongs_to_the_longest_matching_section():
    assert shell.active("/financials/coming-up").key == "coming"
    assert shell.active("/financials/transactions").key == "money"
    assert shell.active("/health/records").key == "health"
    assert shell.active("/").key == "home"
    assert shell.active("/insurance/policies/3").key == "things"
    assert shell.active("/privacy") is None                       # behind the gear


def _menu(html: str) -> list[str]:
    nav = re.search(r'<nav class="pills".*?</nav>', html, re.S).group(0)
    return [re.sub(r"<[^>]+>|\s+\d+$", "", a).strip() for a in re.findall(r'<a class="pill[^"]*"[^>]*>(.*?)</a>', nav, re.S)]


def test_the_header_and_the_phone_bar_on_a_page(client):
    html = client.get("/health/records").text
    assert _menu(html) == ["Home", "Money", "Health", "Things", "Papers", "Coming up", "Waiting for you"]
    assert 'class="pill on" href="/health/family"' in html       # Health opens on the family page since step 10f
    assert "Who: Everyone" in html and 'href="/inbox/add">+ Add a paper' in html and 'href="/settings"' in html
    assert 'class="phonebar"' in html and 'class="pn2 fab" href="/inbox/add"' in html    # the + adds a paper (10h)
    assert 'href="/health/areas"' in html                            # the section's own pages


def test_the_problems_dot_shows_only_when_something_is_wrong(client, session, engine, monkeypatch):
    monkeypatch.setattr("app.db.engine", engine)                    # the menu counts open its own session
    assert 'class="alert" href="/problems"' not in client.get("/").text
    from app.problems import Problem, record_problem
    record_problem(session, area="Scheduled jobs", problem=Problem(
        "Mailbox check didn't finish.", "It couldn't be reached.", "Nothing to do; it tries again."))
    session.commit()
    html = client.get("/").text
    assert 'class="alert" href="/problems"' in html


def test_the_gear_and_more_pages_list_every_page(client):
    settings = client.get("/settings")
    assert settings.status_code == 200
    for label in ("Privacy", "Scheduled jobs", "Bank connections", "What the Hub knows"):
        assert label in settings.text
    more = client.get("/more").text
    assert all(f'href="{l.url}"' in more for l in shell.every_link())


def test_old_urls_still_answer(client):
    for url in ("/ask", "/inbox", "/waiting", "/todos", "/privacy", "/schedules", "/problems", "/wiki",
                "/financials/transactions", "/financials/coming-up", "/health/records", "/house", "/insurance", "/other"):
        assert client.get(url).status_code == 200, url


def test_the_section_pages_sit_with_search_inside_the_page_width(client):
    """PLAN step 11a: the section's own pages and search share one row inside the one content width; type and width
    grow with the screen (the stylesheet's tokens)."""
    html = client.get("/financials/transactions").text
    row = re.search(r'<div class="subbar">(.*?)</form>\s*</div>', html, re.S).group(1)
    assert row.index('class="sectionbar"') < row.index('class="nav-search"')
    assert ">All lines</a>" in row
    css = open("app/static/hub.css").read()
    assert "--page-max: 96rem" in css and "clamp(15px" in css
    assert ".topnav, .subbar, main { max-width: var(--page-max)" in css
    assert "1240px" not in css
