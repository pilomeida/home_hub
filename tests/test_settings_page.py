"""PLAN step 10f: the gear's page shows a card per setting with what it says today, each opening its own page."""

from app import problems as pr
from app.jobs.registry import JOBS
from app.privacy import settings as ps
from app.services.settings_page import settings_page


def test_the_cards_say_what_each_setting_says_today(session):
    p = pr.explain("Mailbox check didn’t finish.", "gateway returned 502: upstream timeout")
    pr.record_problem(session, area="Scheduled jobs", problem=p, ref="job:mailbox_check")
    session.commit()
    s = settings_page(session)
    assert s.problems_open == 1 and s.problems[0].what == "Mailbox check didn’t finish."
    rows = ps.type_rows(session)
    assert sum(n for _, n in s.levels) == len(rows)                  # every kind of paper is counted once
    assert [j.label for j in s.jobs] == [j.label for j in JOBS.values()]
    assert s.banks and all("status" in b for b in s.banks)


def test_the_settings_page_links_every_setting(client, session):
    html = client.get("/settings").text
    for url in ("/problems", "/financials/bank/", "/privacy", "/schedules", "/wiki"):
        assert f'href="{url}"' in html
    assert "Nothing is wrong." in html and "Privacy levels" in html
