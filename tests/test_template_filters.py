"""The one money filter, the one date filter and the one three-part message (PLAN step 9, D4)."""
from datetime import date, datetime

from app.problems import Problem
from app.templating import templates


def _render(source: str, **ctx) -> str:
    return templates.env.from_string(source).render(**ctx)


def test_money_shows_euros_to_the_cent_or_whole():
    assert _render("{{ v|money }}", v=1234.5) == "€1,234.50"
    assert _render("{{ v|money(whole=True) }}", v=1234.5) == "€1,234"


def test_dates_share_one_format_and_a_missing_one_shows_a_dash():
    assert _render("{{ d|display_date }}", d=date(2026, 11, 20)) == "2026-11-20"
    assert _render("{{ d|display_date }}", d=datetime(2026, 11, 20, 9, 5)) == "2026-11-20"
    assert _render("{{ d|display_date }}", d=None) == "—"


def test_one_macro_says_what_why_and_what_to_do_with_its_link_for_a_problem_and_its_http_detail():
    problem = Problem("Not uploaded.", "This kind of document has no privacy level yet.",
                      "Set its level on the Privacy page, then try again.", link="/privacy")
    source = '{% from "_message.html" import three_part %}{{ three_part(p, link_text="Privacy page") }}'
    for p in (problem, problem.as_detail()):
        html = _render(source, p=p)
        assert "<strong>Not uploaded.</strong>" in html
        assert "no privacy level yet." in html and "then try again." in html
        assert '<a href="/privacy">Privacy page</a>' in html


def test_no_template_formats_money_or_dates_by_hand():
    import pathlib
    import re
    hand = re.compile(r"""€\{\{ ?["']\{:,|strftime\(['"]%Y-%m-%d['"]\)|<strong>\{\{ [\w.]+\.what \}\}</strong>""")
    offenders = [str(p) for p in pathlib.Path("app/templates").rglob("*.html")
                 if p.name != "_message.html" and hand.search(p.read_text())]
    assert offenders == []
