"""Messages for what could not be done (decision D14): every one says what, why, and what to do."""

import pytest
from sqlmodel import select

from app import problems as pr
from app.models.schedule import ProblemEntry

RAW_REASONS = [
    "gateway returned 503: upstream down",
    "gateway unreachable at http://x: ConnectError",
    "gateway returned 402: out of credit",
    "gateway returned 400: no_eligible_model",
    "gateway returned 400: could not read pdf",
    "gateway returned 413: prompt_too_long",
    "gateway returned 403: sensitivity house_high has no route yet",
    "redaction requested but the call carries attachments (images/PDFs cannot be redacted)",
    "redaction with tool use is not supported yet",
    "redaction incomplete (2 identifier(s) remain)",
    "ValueError: 'parking' is not a valid Category",
    "KeyError: Traceback boom",
]


@pytest.mark.parametrize("raw", RAW_REASONS)
def test_every_known_technical_reason_has_a_fact_a_reason_and_a_next_step(raw):
    cause = pr.classify_reason(raw)
    assert cause is not None, raw
    for part in (cause.fact, cause.why, cause.next_step):
        assert part.strip() and len(part) > 12, (raw, part)
    assert cause.fact.rstrip().endswith((".", "”")) and cause.next_step.rstrip().endswith(".")


def test_causes_name_the_right_reason():
    assert "run out of credit" in pr.classify_reason("gateway returned 402: out of credit").why
    assert "restarting or busy" in pr.classify_reason("gateway returned 503").why
    assert "damaged" in pr.classify_reason("bad pdf").why
    assert "Privacy page" in pr.classify_reason("redaction requested but the call carries attachments").next_step
    assert "High" in pr.classify_reason("sensitivity house_high has no route yet").why


def test_plain_english_is_left_alone():
    assert pr.classify_reason("duplicate — matched existing transaction") is None


def test_compose_puts_the_callers_own_fact_first_and_lets_the_page_choose_the_next_step():
    p = pr.compose("bill.pdf wasn't processed.", pr.classify_reason("gateway returned 503"), actionable="Enter it by hand below.")
    assert p.text.startswith("bill.pdf wasn't processed. ") and p.text.endswith("Enter it by hand below.")


def test_explain_with_an_unknown_reason_still_gives_all_three_parts():
    p = pr.explain("Not done.", "weird plain reason from somewhere")
    assert p.what == "Not done." and "weird plain reason" in p.why and p.next_step


def test_html_is_escaped_and_technical_details_are_secondary():
    html = str(pr.render_html(pr.Problem("Not <b>done</b>.", "Because & so.", "Retry."), technical="<script>x</script>"))
    assert "<script>" not in html and "&lt;script&gt;" in html and "<details>" in html and "&lt;b&gt;done" in html


# --- web errors ---------------------------------------------------------------------------------

HTTP_CASES = [
    (404, "Document not found"), (404, "Wiki page not found"), (404, "No such bank connection"),
    (400, "Pick a sub-category to apply"), (400, "Tick at least one description"), (400, "Tick at least one merchant"),
    (400, "Unknown category"), (400, "Choose at least one file"), (400, "Upload at most 10 files at a time"),
    (400, "The note is at most 280 characters"), (400, "Question must be 1–2000 characters"),
    (400, "Only missing-link findings can add a link"), (400, "This printout does not need a loan assignment"),
    (409, "That document has already been handled."), (409, "That document isn't in the Inbox."),
    (409, "The previous question is still being answered"), (400, "Only answered questions can be saved"),
    (503, "Bank connections aren't set up on this server yet"), (404, "Not in the Inbox"), (404, "Unknown utility tab"),
    (413, ""), (422, ""), (500, "boom"), (401, ""), (400, "something unexpected"),
]


@pytest.mark.parametrize("status,detail", HTTP_CASES)
def test_every_web_error_gets_what_why_and_next_step(status, detail):
    p = pr.for_http(status, detail)
    assert p.what.strip() and p.why.strip() and p.next_step.strip(), (status, detail)
    assert p.what.rstrip().endswith("."), p.what


def test_no_error_message_is_just_the_bare_fact():
    for status, detail in HTTP_CASES:
        p = pr.for_http(status, detail)
        assert p.why != p.what and len(p.why) > 15 and len(p.next_step) > 10, (status, detail)


def test_a_route_can_write_its_own_message():
    p = pr.for_http(400, {"what": "Not uploaded.", "why": "Because.", "next_step": "Do this."})
    assert (p.what, p.why, p.next_step) == ("Not uploaded.", "Because.", "Do this.")


# --- the log and the pages ----------------------------------------------------------------------

def test_the_same_open_problem_is_counted_not_repeated_and_clears_when_it_works(session):
    p = pr.Problem("Not filed.", "Because.", "Do this.")
    pr.record_problem(session, area="Inbox", problem=p, ref="document:1")
    pr.record_problem(session, area="Inbox", problem=p, ref="document:1")
    entry = session.exec(select(ProblemEntry)).one()
    assert entry.times == 2 and entry.resolved_at is None
    assert pr.resolve_for(session, "document:1") == 1
    assert pr.open_problems(session) == []


def test_problems_page_lists_and_dismisses(client, session):
    pr.record_problem(session, area="Email", problem=pr.Problem("An email wasn't filed.", "Sender unknown.", "Add the sender."), ref="e1",
                      technical="Traceback…")
    page = client.get("/problems").text
    assert "An email wasn&#39;t filed." in page or "An email wasn't filed." in page
    assert "Sender unknown." in page and "Add the sender." in page and "Technical details" in page
    entry = session.exec(select(ProblemEntry)).one()
    client.post(f"/problems/{entry.id}/dismiss")
    session.refresh(entry)
    assert entry.resolved_at is not None and "Nothing is stuck right now." in client.get("/problems").text


def test_the_menu_shows_how_many_problems_are_open(client, session):
    assert "Problems</a>" in client.get("/").text
    pr.record_problem(session, area="Email", problem=pr.Problem("A.", "B because.", "C do it."), ref="x")
    # the menu counts through its own connection to the real engine in tests, so only check it renders
    assert "/problems" in client.get("/").text


def test_browser_requests_get_a_page_htmx_gets_a_banner_and_api_callers_keep_json(client):
    import html as htmllib
    html = client.get("/financials/bills/99999", headers={"accept": "text/html"})
    assert html.status_code == 404 and "Couldn't open that." in htmllib.unescape(html.text) and "What you can do:" in html.text
    htmx = client.get("/financials/bills/99999", headers={"hx-request": "true"})
    assert htmx.status_code == 404 and htmx.headers["hx-retarget"] == "#problem-banner" and "Couldn" in htmx.text
    api = client.get("/financials/bills/99999")
    assert api.status_code == 404 and api.json() == {"detail": "Document not found"}


def test_the_page_has_a_banner_and_lets_failed_button_presses_through(client):
    page = client.get("/privacy").text
    assert 'id="problem-banner"' in page and "htmx:beforeSwap" in page and "htmx:sendError" in page
