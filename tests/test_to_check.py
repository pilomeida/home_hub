"""The "To check" list (decisions D11, D17): the service, the routes (driven through the TestClient with real
models) and the weekly Telegram nudge. Synthetic data only; no network (the Telegram send is the reminder
job's own seam, replaced here)."""

import asyncio
import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlmodel import select
from telegram.error import Forbidden, NetworkError

from app.config import settings
from app.jobs import registry, runners, scheduler
from app.jobs import statement_reminder as reminder_job
from app.models.document import Document, DocumentSource
from app.models.schedule import JobRun
from app.models.transaction import StatementCheck, Transaction, TransactionType
from app.problems import Problem
from app.routers import to_check as to_check_router
from app.services import to_check
from app.services.schedule import Spec
from app.services.to_check import ToCheckError


# ------------------------------------------------------------------ builders ---

def statement(session, name="march.pdf", n=[0]):
    n[0] += 1
    document = Document(filename=name, file_path=f"/tmp/never-read-{n[0]}-{name}", content_hash=f"tc-{n[0]}",
                        source=DocumentSource.MANUAL)
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def line(session, document, description="PINGO DOCE LISBOA", amount=42.5, day=date(2026, 3, 14), *,
         status="needs_check", reason="The amount is partly cut off on the page.", page=2, by=None):
    t = Transaction(document_id=document.id, description=description, amount=amount, paid_date=day,
                    transaction_type=TransactionType.DEBIT, review_status=status, review_reason=reason,
                    source_page=page, checked_by=by, checked_at=datetime(2026, 3, 20, 10, 30) if by else None)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def mismatch(session, document, status="mismatch", resolved=False):
    check = StatementCheck(document_id=document.id, opening_balance=1000.0, closing_balance=1500.0, lines_total=480.0,
                           difference=20.0, status=status, resolved_at=datetime(2026, 3, 21) if resolved else None,
                           resolved_by="Rute" if resolved else None)
    session.add(check)
    session.commit()
    session.refresh(check)
    return check


def three_parts(problem: Problem) -> None:
    """What didn't happen, why, what to do: three non-empty sentences, in plain words (no exception text)."""
    for part in (problem.what, problem.why, problem.next_step):
        assert part.strip() and part.strip()[-1] in ".!?", problem
    assert problem.what.startswith("Not ") or problem.what.startswith("Nothing"), problem
    assert "Traceback" not in problem.text and "Error(" not in problem.text


def refused(call, *args, **kwargs) -> ToCheckError:
    with pytest.raises(ToCheckError) as caught:
        call(*args, **kwargs)
    three_parts(caught.value.problem)
    return caught.value


def fresh(session, row):
    session.expire_all()
    return session.get(type(row), row.id)


# ------------------------------------------------------------------ what is waiting ---

def test_items_are_exactly_the_open_ones_grouped_by_statement_in_page_and_date_order(session):
    a, b, c = statement(session, "a.pdf"), statement(session, "b.pdf"), statement(session, "c.pdf")
    late = line(session, a, "LATE", day=date(2026, 3, 30), page=1)
    early_p2 = line(session, a, "EARLY P2", day=date(2026, 3, 2), page=2)
    early_p1 = line(session, a, "EARLY P1", day=date(2026, 3, 5), page=1)
    no_page = line(session, a, "NO PAGE", page=None)
    line(session, a, "sure", status=None)                      # nothing to check
    line(session, a, "said right", status="confirmed", by="Rute")
    line(session, a, "said not real", status="rejected", by="Rute")
    only_b = mismatch(session, b)
    mismatch(session, c, status="accepted", resolved=True)     # looked at already
    mismatch(session, statement(session, "d.pdf"), status="ok")
    mismatch(session, statement(session, "e.pdf"), status="unverified")
    resolved_mismatch = statement(session, "f.pdf")
    mismatch(session, resolved_mismatch, status="mismatch", resolved=True)

    groups = to_check.items(session)

    assert [g.document.filename for g in groups] == ["a.pdf", "b.pdf"]
    assert [t.description for t in groups[0].lines] == ["EARLY P1", "LATE", "EARLY P2", "NO PAGE"]
    assert groups[0].mismatch is None and groups[1].lines == [] and groups[1].mismatch.id == only_b.id
    assert to_check.count(session) == 5                        # four held lines + one statement difference
    assert {t.id for g in groups for t in g.lines} == {late.id, early_p1.id, early_p2.id, no_page.id}


def test_nothing_open_means_zero(session):
    line(session, statement(session), status=None)
    assert to_check.items(session) == [] and to_check.count(session) == 0


def test_a_held_line_counts_nowhere_until_it_is_decided(session):
    document = statement(session)
    kept, dropped = line(session, document), line(session, document, "NOT REAL")

    def counted():
        return {t.id for t in session.exec(select(Transaction).where(Transaction.counts())).all()}

    assert counted() == set()
    to_check.confirm(session, kept.id, "Rute")
    to_check.reject(session, dropped.id, "Rute")
    assert counted() == {kept.id}


# ------------------------------------------------------------------ the actions ---

def test_confirm_changes_only_the_review_fields_and_records_who_and_when(session):
    t = line(session, statement(session))
    before = (t.description, t.amount, t.paid_date, t.review_reason, t.source_page)
    to_check.confirm(session, t.id, "rute@example.test")
    t = fresh(session, t)
    assert (t.review_status, t.checked_by) == ("confirmed", "rute@example.test")
    assert t.checked_at is not None and (datetime.utcnow() - t.checked_at).total_seconds() < 60
    assert (t.description, t.amount, t.paid_date, t.review_reason, t.source_page) == before
    assert t.review_original_json is None


def test_reject_marks_not_a_real_line_and_records_who_and_when(session):
    t = line(session, statement(session))
    to_check.reject(session, t.id, "pedro@example.test")
    t = fresh(session, t)
    assert (t.review_status, t.checked_by) == ("rejected", "pedro@example.test") and t.checked_at is not None
    assert t.review_original_json is None and t.amount == 42.5


def test_correct_applies_the_values_keeps_the_originals_and_confirms(session):
    t = line(session, statement(session))
    to_check.correct(session, t.id, "rute@example.test", date="2026-03-15", description="  PINGO  DOCE ", amount="47,80")
    t = fresh(session, t)
    assert (t.review_status, t.checked_by) == ("confirmed", "rute@example.test") and t.checked_at is not None
    assert (t.paid_date, t.description, t.amount) == (date(2026, 3, 15), "PINGO DOCE", 47.8)
    assert json.loads(t.review_original_json) == {"date": "2026-03-14", "description": "PINGO DOCE LISBOA", "amount": 42.5}


def test_correct_changes_only_what_was_given(session):
    t = line(session, statement(session))
    to_check.correct(session, t.id, "Rute", amount=50)
    t = fresh(session, t)
    assert (t.paid_date, t.description, t.amount) == (date(2026, 3, 14), "PINGO DOCE LISBOA", 50.0)


def test_a_later_correction_never_overwrites_the_first_originals(session):
    t = line(session, statement(session))
    to_check.correct(session, t.id, "Rute", amount="47.80")
    held_again = fresh(session, t)
    held_again.review_status = "needs_check"                   # e.g. a re-run of the check held it once more
    session.add(held_again)
    session.commit()
    to_check.correct(session, t.id, "Pedro", amount="48.00")
    t = fresh(session, t)
    assert t.amount == 48.0 and json.loads(t.review_original_json)["amount"] == 42.5


@pytest.mark.parametrize("values,fragment", [
    ({"date": "2026-02-30"}, "not a real date"),
    ({"date": "yesterday"}, "not a real date"),
    ({"amount": "twelve"}, "not an amount"),
    ({"amount": "0"}, "not an amount"),
    ({"amount": "nan"}, "not an amount"),
    ({"description": "   "}, "description is empty"),
    ({"description": "x" * 201}, "longer than"),
    ({"date": "2026-03-14", "description": "PINGO DOCE LISBOA", "amount": "42.50"}, "Nothing was changed"),
])
def test_invalid_input_is_refused_in_three_parts_and_changes_nothing(session, values, fragment):
    t = line(session, statement(session))
    error = refused(to_check.correct, session, t.id, "Rute", **values)
    assert fragment in error.problem.text and error.status == 422
    t = fresh(session, t)
    assert (t.review_status, t.checked_by, t.checked_at, t.review_original_json) == ("needs_check", None, None, None)
    assert (t.paid_date, t.description, t.amount) == (date(2026, 3, 14), "PINGO DOCE LISBOA", 42.5)


@pytest.mark.parametrize("first", ["confirm", "reject"])
@pytest.mark.parametrize("second", ["confirm", "reject", "correct"])
def test_a_line_cannot_be_decided_twice(session, first, second):
    t = line(session, statement(session))
    getattr(to_check, first)(session, t.id, "Rute")
    decided = fresh(session, t)
    snapshot = (decided.review_status, decided.checked_by, decided.checked_at, decided.amount, decided.review_original_json)

    args = {"amount": "99"} if second == "correct" else {}
    error = refused(getattr(to_check, second), session, t.id, "Pedro", **args)

    assert "already decided" in error.problem.why and "Rute" in error.problem.why and error.status == 409
    again = fresh(session, t)
    assert (again.review_status, again.checked_by, again.checked_at, again.amount, again.review_original_json) == snapshot


def test_a_line_that_was_never_held_or_does_not_exist_is_refused(session):
    sure = line(session, statement(session), status=None)
    assert "never held" in refused(to_check.confirm, session, sure.id, "Rute").problem.why
    assert fresh(session, sure).review_status is None
    gone = refused(to_check.reject, session, 99999, "Rute")
    assert gone.status == 404 and "isn't in the Hub" in gone.problem.why


def test_accept_difference_records_who_and_when_and_takes_it_off_the_list(session):
    document = statement(session)
    check = mismatch(session, document)
    to_check.accept_difference(session, document.id, "rute@example.test")
    check = fresh(session, check)
    assert (check.status, check.resolved_by) == ("accepted", "rute@example.test") and check.resolved_at is not None
    assert (check.opening_balance, check.closing_balance, check.lines_total, check.difference) == (1000.0, 1500.0, 480.0, 20.0)
    assert to_check.items(session) == [] and to_check.count(session) == 0


def test_a_difference_cannot_be_accepted_twice_or_when_there_is_none(session):
    document = statement(session)
    mismatch(session, document)
    to_check.accept_difference(session, document.id, "Rute")
    first = fresh(session, session.exec(select(StatementCheck)).one())
    error = refused(to_check.accept_difference, session, document.id, "Pedro")
    assert "already looked at" in error.problem.why and "Rute" in error.problem.why
    assert fresh(session, first).resolved_by == "Rute"

    ok_doc = statement(session, "ok.pdf")
    ok = mismatch(session, ok_doc, status="ok")
    assert "not flagged" in refused(to_check.accept_difference, session, ok_doc.id, "Rute").problem.why
    assert fresh(session, ok).status == "ok" and fresh(session, ok).resolved_by is None
    assert refused(to_check.accept_difference, session, 99999, "Rute").status == 404


# ------------------------------------------------------------------ the page ---

@pytest.fixture()
def hub(client, engine, monkeypatch):
    """The client, with the menu's own-session count pointed at the test database."""
    monkeypatch.setattr("app.db.engine", engine)
    return client


def test_the_page_shows_exactly_the_open_items_with_the_source_page_beside_them(hub, session):
    pdf = statement(session, "march.pdf")
    held = line(session, pdf, "PINGO DOCE LISBOA", 42.5, page=3, reason="The amount is partly cut off on the page.")
    line(session, pdf, "ALREADY SURE", status=None)
    line(session, pdf, "ALREADY CONFIRMED", status="confirmed", by="Rute")
    other = statement(session, "april.pdf")
    mismatch(session, other)

    html = hub.get("/to-check").text

    assert "PINGO DOCE LISBOA" in html and "ALREADY SURE" not in html and "ALREADY CONFIRMED" not in html
    assert f'<iframe src="/static/documents/{pdf.file_path.rsplit("/", 1)[-1]}#page=3"' in html
    assert "Page 3 of the statement" in html
    assert "The amount is partly cut off on the page." in html
    assert 'class="tc-doubt">€42.50' in html and 'class="tc-doubt">14 Mar 2026' not in html     # the reason names the amount
    for action in ("Looks right", "Fix the value", "Not a real line"):
        assert action in html
    assert "The lines add up to €480.00 but the statement says €500.00 (difference €20.00)." in html
    assert "The difference is fine" in html
    assert "To check (2)" in html


def test_images_are_shown_as_images(hub, session):
    photo = statement(session, "scan.jpg")
    line(session, photo, "SCANNED LINE")
    html = hub.get("/to-check").text
    assert "<img src=" in html and "<iframe" not in html


def test_the_whole_line_is_highlighted_when_the_reason_names_no_field(hub, session):
    line(session, statement(session), reason="The printing is blurry here.")
    html = hub.get("/to-check").text
    assert html.count("tc-doubt") >= 3 + 1       # three highlighted values (+ the stylesheet rule)


def test_the_list_is_a_second_section_on_the_inbox_page(hub, session):
    line(session, statement(session), "PINGO DOCE LISBOA")
    html = hub.get("/inbox").text
    assert "Nothing waiting" in html                                  # the Inbox itself is untouched
    assert html.index("Nothing waiting") < html.index("<h2>To check (1)</h2>") < html.index("PINGO DOCE LISBOA")


def test_the_inbox_says_so_when_there_is_nothing_to_check(hub):
    html = hub.get("/inbox").text
    assert "<h2>To check</h2>" in html and "Nothing to check" in html


def test_an_htmx_request_for_the_list_gets_a_fragment_not_a_page(hub, session):
    line(session, statement(session))
    fragment = hub.get("/to-check", headers={"HX-Request": "true"}).text
    assert fragment.lstrip().startswith("<section") and "<html" not in fragment and "<nav" not in fragment
    assert "<html" in hub.get("/to-check").text


def test_the_menu_link_appears_only_when_something_is_waiting(hub, session):
    assert 'href="/to-check"' not in hub.get("/inbox").text and "To check (" not in hub.get("/").text
    document = statement(session)
    t1, t2 = line(session, document), line(session, document, "SECOND")
    html = hub.get("/inbox").text
    assert '<a href="/to-check">To check (2)</a>' in html and '<a href="/inbox">Inbox</a>' in html   # Inbox count unchanged
    to_check.confirm(session, t1.id, "Rute")
    to_check.confirm(session, t2.id, "Rute")
    assert 'href="/to-check"' not in hub.get("/inbox").text


# ------------------------------------------------------------------ pressing the buttons ---

def test_looks_right_answers_with_a_fragment_and_confirms(hub, session):
    t = line(session, statement(session))
    response = hub.post(f"/to-check/lines/{t.id}/confirm", headers={"HX-Request": "true"})
    assert response.status_code == 200 and "<html" not in response.text
    assert f'id="tc-line-{t.id}"' in response.text and "confirmed by someone in the family" in response.text
    t = fresh(session, t)
    assert t.review_status == "confirmed" and t.checked_by == "someone in the family" and t.checked_at is not None


def test_not_a_real_line_rejects(hub, session):
    t = line(session, statement(session))
    response = hub.post(f"/to-check/lines/{t.id}/reject", headers={"HX-Request": "true"})
    assert response.status_code == 200 and "marked as not a real line" in response.text
    assert fresh(session, t).review_status == "rejected"


def test_fix_the_value_opens_the_form_and_saving_stores_it_with_the_original(hub, session):
    t = line(session, statement(session))
    form = hub.get(f"/to-check/lines/{t.id}/edit", headers={"HX-Request": "true"}).text
    assert 'name="date" value="2026-03-14"' in form and 'name="amount" value="42.50"' in form and "<html" not in form

    response = hub.post(f"/to-check/lines/{t.id}/correct", data={"date": "2026-03-15", "description": "PINGO DOCE", "amount": "47,80"},
                        headers={"HX-Request": "true"})

    assert response.status_code == 200 and "corrected and confirmed" in response.text and "€47.80" in response.text
    t = fresh(session, t)
    assert (t.paid_date, t.description, t.amount, t.review_status) == (date(2026, 3, 15), "PINGO DOCE", 47.8, "confirmed")
    assert json.loads(t.review_original_json) == {"date": "2026-03-14", "description": "PINGO DOCE LISBOA", "amount": 42.5}


def test_a_bad_value_comes_back_in_the_card_with_what_was_typed_and_nothing_changes(hub, session):
    t = line(session, statement(session))
    response = hub.post(f"/to-check/lines/{t.id}/correct", data={"date": "2026-02-30", "description": "PINGO", "amount": "abc"},
                        headers={"HX-Request": "true"})
    assert response.status_code == 422 and "<html" not in response.text
    assert 'class="problem"' in response.text and "Not saved." in response.text and "not a real date" in response.text
    assert 'value="2026-02-30"' in response.text and 'value="abc"' in response.text and "Save the fix" in response.text
    t = fresh(session, t)
    assert (t.review_status, t.amount, t.review_original_json) == ("needs_check", 42.5, None)


def test_pressing_a_button_on_a_line_someone_else_already_decided_says_so_and_changes_nothing(hub, session):
    t = line(session, statement(session))
    to_check.confirm(session, t.id, "rute@example.test")
    for action, data in (("confirm", None), ("reject", None), ("correct", {"date": "2026-03-15", "description": "X", "amount": "1"})):
        response = hub.post(f"/to-check/lines/{t.id}/{action}", data=data, headers={"HX-Request": "true"})
        assert response.status_code == 409 and "<html" not in response.text, action
        assert "Someone has already decided" in response.text and "rute@example.test" in response.text, action
        assert f'id="tc-line-{t.id}"' in response.text
    t = fresh(session, t)
    assert (t.review_status, t.checked_by, t.amount, t.review_original_json) == ("confirmed", "rute@example.test", 42.5, None)


def test_an_unknown_line_is_a_three_part_message_in_the_card(hub):
    response = hub.post("/to-check/lines/99999/confirm", headers={"HX-Request": "true"})
    assert response.status_code == 404 and "Not confirmed." in response.text and "in the Hub any more" in response.text
    assert hub.get("/to-check/lines/99999/edit", headers={"HX-Request": "true"}).status_code == 404


def test_the_difference_is_fine_accepts_and_a_second_press_is_refused(hub, session):
    document = statement(session)
    check = mismatch(session, document)
    response = hub.post(f"/to-check/statements/{document.id}/accept", headers={"HX-Request": "true"})
    assert response.status_code == 200 and "accepted by someone in the family" in response.text
    check = fresh(session, check)
    assert check.status == "accepted" and check.resolved_by == "someone in the family" and check.resolved_at is not None
    again = hub.post(f"/to-check/statements/{document.id}/accept", headers={"HX-Request": "true"})
    assert again.status_code == 409 and "Not accepted." in again.text and "already looked at" in again.text
    assert hub.get("/to-check").text.count("The difference is fine") == 0


def test_who_is_the_signed_in_person_when_there_is_one():
    signed_in = SimpleNamespace(state=SimpleNamespace(user_email="rute@example.test"))
    assert to_check_router._who(signed_in) == "rute@example.test"
    assert to_check_router._who(SimpleNamespace(state=SimpleNamespace())) == "someone in the family"


# ------------------------------------------------------------------ the weekly nudge ---

def test_the_nudge_is_registered_weekly_on_monday_at_nine():
    job = registry.JOBS["check_nudge"]
    assert job.label == "Things to check" and job.default == Spec(kind="weekly", weekday=0, time_of_day="09:00")
    assert len(job.description) > 40 and job.run is runners.run_check_nudge and asyncio.iscoroutinefunction(job.run)


@pytest.fixture()
def telegram(monkeypatch):
    """A configured Telegram whose network send (the statement reminder's own seam) is replaced."""
    sent = []
    monkeypatch.setattr(settings, "HUB_TELEGRAM_BOT_TOKEN", "synthetic-token")
    monkeypatch.setattr(settings, "HUB_REMINDER_TELEGRAM_NAME", "Pedro")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://hub.example.test")
    monkeypatch.setenv("HUB_TELEGRAM_ALLOWED_USERS", "111:Rute,222:Pedro")
    behaviour = {"raise": None}

    def send(token, chat_id, text):
        if behaviour["raise"] is not None:
            raise behaviour["raise"]
        sent.append((token, chat_id, text))
    monkeypatch.setattr(reminder_job, "telegram_send", send)
    return sent, behaviour


def three_sentences(text: str) -> None:
    assert len([x for x in text.replace("? ", ". ").split(". ") if x.strip()]) >= 3, text


@pytest.mark.asyncio
async def test_the_nudge_sends_nothing_when_nothing_waits(session, telegram):
    sent, _ = telegram
    line(session, statement(session), status="confirmed", by="Rute")
    out = await runners.run_check_nudge(session, "schedule")
    assert (out.outcome, out.summary) == ("ok", "Nothing to check this week.") and sent == []


@pytest.mark.asyncio
async def test_the_nudge_sends_one_message_with_the_count_and_link_to_the_reminders_recipient(session, telegram):
    sent, _ = telegram
    document = statement(session)
    line(session, document)
    line(session, document, "SECOND")
    mismatch(session, statement(session, "b.pdf"))
    out = await runners.run_check_nudge(session, "schedule")
    assert out.outcome == "ok" and "Pedro" in out.summary
    assert sent == [("synthetic-token", 222, "3 things are waiting to be checked in the Hub: https://hub.example.test/to-check")]


@pytest.mark.asyncio
async def test_the_nudge_says_one_thing_in_the_singular(session, telegram):
    sent, _ = telegram
    line(session, statement(session))
    await runners.run_check_nudge(session, "manual")
    assert sent[0][2] == "1 thing is waiting to be checked in the Hub: https://hub.example.test/to-check"


@pytest.mark.asyncio
async def test_the_nudge_follows_the_reminders_recipient_setting(session, telegram, monkeypatch):
    sent, _ = telegram
    monkeypatch.setattr(settings, "HUB_REMINDER_TELEGRAM_NAME", "Rute")
    line(session, statement(session))
    await runners.run_check_nudge(session, "schedule")
    assert [chat for _, chat, _ in sent] == [111]


@pytest.mark.asyncio
async def test_the_nudge_without_a_bot_token_fails_in_three_parts_and_sends_nothing(session, telegram, monkeypatch):
    sent, _ = telegram
    monkeypatch.setattr(settings, "HUB_TELEGRAM_BOT_TOKEN", "")
    line(session, statement(session))
    out = await runners.run_check_nudge(session, "schedule")
    assert out.outcome == "failed" and out.summary.startswith("The “things to check” message wasn't sent.")
    assert "no bot token" in out.summary and "Tell Pedro" in out.summary
    three_sentences(out.summary)
    assert sent == []


@pytest.mark.asyncio
async def test_the_nudge_with_an_unreadable_list_or_no_such_person_fails_in_three_parts(session, telegram, monkeypatch):
    line(session, statement(session))
    monkeypatch.setenv("HUB_TELEGRAM_ALLOWED_USERS", "not-a-number:Pedro")
    out = await runners.run_check_nudge(session, "schedule")
    assert out.outcome == "failed" and "can't be read" in out.summary and "HUB_TELEGRAM_ALLOWED_USERS" in out.summary
    three_sentences(out.summary)
    monkeypatch.setenv("HUB_TELEGRAM_ALLOWED_USERS", "111:Rute")
    out = await runners.run_check_nudge(session, "schedule")
    assert out.outcome == "failed" and "No allowed Telegram user is named 'Pedro'" in out.summary
    three_sentences(out.summary)


@pytest.mark.asyncio
@pytest.mark.parametrize("error,fragment", [
    (Forbidden("bot was blocked by the user"), "press Start"),
    (NetworkError("httpx.ConnectError: All connection attempts failed"), "couldn't be reached"),
])
async def test_the_nudge_send_failures_say_why_and_what_to_do(session, telegram, error, fragment):
    sent, behaviour = telegram
    line(session, statement(session))
    behaviour["raise"] = error
    out = await runners.run_check_nudge(session, "schedule")
    assert out.outcome == "failed" and out.summary.startswith("The “things to check” message wasn't sent.") and fragment in out.summary
    three_sentences(out.summary)
    behaviour["raise"] = None
    assert (await runners.run_check_nudge(session, "schedule")).outcome == "ok" and len(sent) == 1


@pytest.mark.asyncio
async def test_the_scheduler_runs_it_and_records_the_outcome(session, telegram):
    sent, _ = telegram
    line(session, statement(session))
    run = await scheduler.run_job(session, "check_nudge", "manual")
    assert run.outcome == "ok" and len(sent) == 1
    assert session.exec(select(JobRun).where(JobRun.job == "check_nudge")).one().outcome == "ok"
