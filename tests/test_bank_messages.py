"""Bank sync, bank pages, bills and the statement reminder follow the messages rule (D14): every
warning says what didn't happen, why, and what to do, and a failure nobody is watching lands on the
Problems page and clears itself when the thing works again.

Errors come from the production path: the real EnableBankingClient talking to an httpx MockTransport
(no network), the real BankApiError, the real sync_link/run_all, the real record_problem."""

import asyncio
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlmodel import select

from app import problems as pr
from app.jobs import bank_sync as bank_job
from app.jobs import statement_reminder as reminder_job
from app.models.account import Account
from app.models.bank import BankAccountLink, BankApiCall, BankConnection, BankConnectionStatus
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.position import BalanceSnapshot
from app.models.schedule import ProblemEntry
from app.models.transaction import Transaction
from app.services.bankapi import messages as msg
from app.services.bankapi.client import EnableBankingClient
from app.services.bankapi.connect import complete_connection
from app.services.bankapi.sync import sync_link
from app.services import statement_reminder as reminder


@pytest.fixture(scope="module")
def pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def real_client(pem, handler) -> EnableBankingClient:
    return EnableBankingClient("app-123", pem, transport=httpx.MockTransport(handler))


def answers(status, body=None):
    return lambda request: httpx.Response(status, json=body if body is not None else {})


OK_NO_ROWS = answers(200, {"transactions": []})


async def fake_classify(session, transaction, client=None):
    transaction.merchant_id = None


def run(coro):
    return asyncio.run(coro)


def make_link(session, bank="Santander Totta", state="st-msg"):
    conn = BankConnection(bank_name=bank, country="PT", state=state, status=BankConnectionStatus.ACTIVE)
    session.add(conn); session.commit(); session.refresh(conn)
    acct = Account(name="Conta Santander", institution="Santander")
    session.add(acct); session.commit(); session.refresh(acct)
    link = BankAccountLink(connection_id=conn.id, bank_account_uid="u1", display_name="Conta",
                           account_id=acct.id)
    session.add(link); session.commit(); session.refresh(link)
    return conn, link


def entries(session, ref=None, open_only=False):
    q = select(ProblemEntry)
    if ref:
        q = q.where(ProblemEntry.ref == ref)
    rows = list(session.exec(q))
    return [r for r in rows if r.resolved_at is None] if open_only else rows


def assert_three_parts(what, why, next_step):
    for part in (what, why, next_step):
        assert part and part.strip()
    assert what.rstrip().endswith(".") and why != what and len(why) > 20 and len(next_step) > 20, (what, why, next_step)


def assert_entry_is_complete(entry):
    assert_three_parts(entry.what, entry.why, entry.next_step)


# --- every message builder ----------------------------------------------------------------------------

BUILDERS = [
    msg.consent_expired("Santander Totta"), msg.consent_expiring("Revolut", 9), msg.consent_expiring("Revolut", 1),
    msg.consent_expiring("Revolut", 0),
    msg.daily_budget_used("Conta (Santander Totta)"), msg.bank_rate_limited("Conta (Santander Totta)"),
    msg.bank_not_answering("Conta (Santander Totta)"), msg.bank_not_answering("Conta (Santander Totta)", reached=False),
    msg.save_failed("Conta (Santander Totta)"), msg.unexpected_failure("Conta (Santander Totta)"),
    msg.not_updated_for_days("Conta (Santander Totta)", datetime(2026, 10, 1, 9, 0)),
    msg.not_updated_for_days("Conta (Santander Totta)", None),
    msg.last_attempt_failed("Conta (Santander Totta)", "Santander Totta"),
    msg.nothing_linked("Santander Totta"), msg.account_not_linked("Santander Totta"), msg.hub_busy(),
    msg.waiting_for_approval("Revolut"), msg.connect_not_started("The bank said it couldn't."),
    msg.unknown_bank(), msg.returned_without_finishing(), msg.link_used_or_expired(),
    msg.connection_not_finished(), msg.unknown_account_choice(3), msg.sync_setup_missing(),
    msg.topups_not_linked(),
    reminder.problem_no_token(), reminder.problem_bad_user_list(), reminder.problem_no_recipient("Pedro"),
    reminder.problem_send_failed(),
]


@pytest.mark.parametrize("problem", BUILDERS, ids=lambda p: p.what)
def test_every_bank_message_has_what_why_and_a_next_step(problem):
    assert_three_parts(problem.what, problem.why, problem.next_step)


def test_the_bank_not_set_up_detail_is_a_complete_route_message():
    p = pr.for_http(503, msg.bank_not_set_up())
    assert_three_parts(p.what, p.why, p.next_step)


def test_an_expired_consent_says_so_and_points_to_where_to_renew():
    p = msg.consent_expired("Santander Totta")
    assert "expired" in p.text and "nothing will sync until you renew" in p.text
    assert p.link == "/financials/bank/" and "Renew" in p.next_step


def test_a_rate_limit_stop_names_the_limit_the_retry_and_that_nothing_is_lost():
    for p in (msg.daily_budget_used("Conta"), msg.bank_rate_limited("Conta")):
        assert "4 requests per" in p.why and "day" in p.why
        assert "will try again" in p.next_step and "nothing is lost" in p.next_step.lower()


# --- sync failures from the real client -> message, stored error and Problems log ----------------------

def test_bank_rate_limit_is_explained_logged_and_cleared_by_the_next_success(session, pem):
    conn, link = make_link(session)
    limited = real_client(pem, answers(429, {"error": "ASPSP_RATE_LIMIT_EXCEEDED", "message": "slow down"}))
    result = run(sync_link(session, limited, link, today=date(2026, 10, 4)))
    assert "4 requests per day" in result.error and "will try again" in result.error
    assert link.last_error == result.error and link.last_synced_at is None

    (entry,) = entries(session, msg.link_ref(link.id))
    assert entry.area == "Bank sync" and entry.resolved_at is None
    assert_entry_is_complete(entry)
    assert "429" in entry.technical and "slow down" in entry.technical      # raw text only as technical detail
    assert "429" not in entry.why

    again = run(sync_link(session, real_client(pem, OK_NO_ROWS), link, today=date(2026, 10, 4),
                          classify=fake_classify))
    assert again.error is None
    session.expire_all()
    assert session.get(BankAccountLink, link.id).last_error is None
    assert entries(session, msg.link_ref(link.id), open_only=True) == []
    assert entries(session, msg.link_ref(link.id))[0].resolved_by == "resolved by itself"


def test_a_repeating_failure_is_counted_not_duplicated(session, pem):
    conn, link = make_link(session)
    limited = real_client(pem, answers(429, {"error": "RATE_LIMIT", "message": "x"}))
    for _ in range(2):
        run(sync_link(session, limited, link, today=date(2026, 10, 4)))
    (entry,) = entries(session, msg.link_ref(link.id))
    assert entry.times == 2


def test_expired_consent_stops_the_connection_logs_it_and_renewal_clears_it(session, pem):
    conn, link = make_link(session)
    dead = real_client(pem, answers(401, {"error": "SESSION_NOT_FOUND", "message": "gone"}))
    result = run(sync_link(session, dead, link, today=date(2026, 10, 4)))
    assert session.get(BankConnection, conn.id).status == BankConnectionStatus.EXPIRED
    assert "expired" in result.error and "nothing will sync until you renew" in result.error

    (entry,) = entries(session, msg.connection_ref(conn.id))
    assert entry.area == "Bank sync" and entry.link == "/financials/bank/"
    assert_entry_is_complete(entry)
    assert entries(session, msg.link_ref(link.id)) == []        # logged once, on the connection

    # Renewal: a new authorization of the same bank (real complete_connection, bank answers are synthetic)
    new = BankConnection(bank_name="Santander Totta", country="PT", state="renew-1",
                         status=BankConnectionStatus.PENDING)
    session.add(new); session.commit()

    class Bank:
        async def create_session(self, code):
            from app.services.bankapi.client import BankSession, BankSessionAccount
            return BankSession("sess-2", [BankSessionAccount("u1", None, "Conta", "EUR")],
                               datetime(2027, 3, 1, tzinfo=timezone.utc))

    run(complete_connection(session, Bank(), state="renew-1", code="c"))
    session.expire_all()
    assert entries(session, msg.connection_ref(conn.id), open_only=True) == []
    assert entries(session, msg.connection_ref(conn.id))[0].resolved_by == "renewed"


def test_the_bank_not_answering_is_explained_for_a_server_error_and_for_no_connection(session, pem):
    conn, link = make_link(session)
    broken = real_client(pem, answers(500, {"message": "upstream"}))
    result = run(sync_link(session, broken, link, today=date(2026, 10, 4)))
    assert "wasn't updated this time" in result.error and "tell Pedro" in result.error

    def unreachable(request):
        raise httpx.ConnectError("no route to host")

    result = run(sync_link(session, real_client(pem, unreachable), link, today=date(2026, 10, 4)))
    assert "couldn't reach the bank" in result.error
    (entry,) = entries(session, msg.link_ref(link.id), open_only=True)     # same thing, counted
    assert entry.times == 2 and "couldn't reach the bank" in entry.why      # the latest reason wins
    assert "ConnectError" in entry.technical
    assert_entry_is_complete(entry)


def test_spent_daily_budget_is_logged_without_marking_the_account_failed(session, pem):
    conn, link = make_link(session)
    now = datetime.utcnow()
    for i in range(4):
        session.add(BankApiCall(link_id=link.id, called_at=now - timedelta(hours=i), kind="transactions"))
    session.commit()
    result = run(sync_link(session, real_client(pem, OK_NO_ROWS), link, today=date(2026, 10, 4)))
    assert result.skipped_quota and result.error is None
    assert "4 requests per account per day" in result.problem.why
    assert link.last_error is None
    (entry,) = entries(session, msg.link_ref(link.id), open_only=True)
    assert_entry_is_complete(entry)

    session.query(BankApiCall).delete()
    session.commit()
    run(sync_link(session, real_client(pem, OK_NO_ROWS), link, today=date(2026, 10, 4), classify=fake_classify))
    assert entries(session, msg.link_ref(link.id), open_only=True) == []


def test_a_failure_while_saving_is_explained_logged_with_its_traceback_and_cleared(session, pem, monkeypatch):
    import app.services.bankapi.sync as sync_mod
    conn, link = make_link(session)
    row = {"entry_reference": "r1", "status": "BOOK", "credit_debit_indicator": "DBTR",
           "transaction_amount": {"amount": "10.00", "currency": "EUR"}, "booking_date": "2026-09-29",
           "creditor": {"name": "Fine"}}
    real = sync_mod._provider
    monkeypatch.setattr(sync_mod, "_provider", lambda raw, credit: (_ for _ in ()).throw(RuntimeError("boom")))
    result = run(sync_link(session, real_client(pem, answers(200, {"transactions": [row]})), link,
                           today=date(2026, 10, 4), classify=fake_classify))
    assert "only partly updated" in result.error and "fetches them again" in result.error
    (entry,) = entries(session, msg.link_ref(link.id), open_only=True)
    assert_entry_is_complete(entry)
    assert "RuntimeError" in entry.technical and "RuntimeError" not in entry.why

    monkeypatch.setattr(sync_mod, "_provider", real)
    run(sync_link(session, real_client(pem, answers(200, {"transactions": [row]})), link,
                  today=date(2026, 10, 4), classify=fake_classify))
    assert entries(session, msg.link_ref(link.id), open_only=True) == []
    assert session.query(Transaction).count() == 1


def test_a_dry_run_leaves_nothing_on_the_problems_page(session, pem):
    conn, link = make_link(session)
    result = run(sync_link(session, real_client(pem, answers(429, {"error": "RATE_LIMIT", "message": "x"})),
                           link, dry_run=True, today=date(2026, 10, 4)))
    assert result.error and entries(session) == [] and link.last_error is None


# --- the job -----------------------------------------------------------------------------------------

def test_one_account_blowing_up_in_the_job_is_logged_and_the_others_still_run(session, monkeypatch):
    conn, first = make_link(session)
    second = BankAccountLink(connection_id=conn.id, bank_account_uid="u2", display_name="Cartão",
                             account_id=first.account_id)
    session.add(second); session.commit(); session.refresh(second)
    from app.services.bankapi.sync import SyncResult

    async def flaky(session, client, link, **kw):
        if link.id == first.id:
            raise RuntimeError("kaput")
        return SyncResult(created=1)

    monkeypatch.setattr(bank_job, "sync_link", flaky)
    results = run(bank_job.run_all(session, object(), scheduled=True))
    assert results[second.id].created == 1
    assert "wasn't updated this time" in results[first.id].error
    (entry,) = entries(session, msg.link_ref(first.id), open_only=True)
    assert entry.area == "Bank sync" and "kaput" in entry.technical
    assert_entry_is_complete(entry)


def test_check_consents_logs_expiry_and_expiring_soon_and_clears_them_when_renewed(session):
    now = datetime(2026, 10, 4, 9, 0)
    soon = BankConnection(bank_name="Revolut", country="LT", state="s-rev", status=BankConnectionStatus.ACTIVE,
                          valid_until=now + timedelta(days=9, hours=3))
    gone = BankConnection(bank_name="Santander Totta", country="PT", state="s-san",
                          status=BankConnectionStatus.EXPIRED)
    session.add(soon); session.add(gone); session.commit(); session.refresh(soon); session.refresh(gone)

    bank_job.check_consents(session, now)
    (expiring,) = entries(session, msg.expiring_ref(soon.id), open_only=True)
    (expired,) = entries(session, msg.connection_ref(gone.id), open_only=True)
    assert "expires in 9 days" in expiring.what and "expired" in expired.what
    for e in (expiring, expired):
        assert e.link == "/financials/bank/"
        assert_entry_is_complete(e)

    # the same warning on the next run is counted, not duplicated
    bank_job.check_consents(session, now)
    assert expiring.times == 2 or session.get(ProblemEntry, expiring.id).times == 2

    soon.valid_until = now + timedelta(days=170)         # renewed
    session.add(soon); session.commit()
    bank_job.check_consents(session, now)
    assert entries(session, msg.expiring_ref(soon.id), open_only=True) == []


def test_missing_bank_keys_are_only_a_problem_once_a_bank_is_connected(session):
    bank_job.note_setup(session, False)
    assert entries(session) == []
    conn, link = make_link(session)
    bank_job.note_setup(session, False)
    (entry,) = entries(session, "bank-sync:setup", open_only=True)
    assert entry.area == "Bank sync" and "keys" in entry.why
    assert_entry_is_complete(entry)
    bank_job.note_setup(session, True)
    assert entries(session, "bank-sync:setup", open_only=True) == []


# --- notices on the bank pages and Bills & Bank ------------------------------------------------------------

def test_bank_notices_are_complete_messages(session, pem):
    from app.routers.bank import bank_notices
    now = datetime(2026, 10, 10, 12, 0)

    soon_conn, soon_link = make_link(session, "Santander Totta", "n1")
    soon_conn.valid_until = now + timedelta(days=3, hours=1)
    stale_conn, stale_link = make_link(session, "Revolut", "n2")
    stale_link.last_synced_at = now - timedelta(days=3)
    session.add(soon_conn); session.add(stale_link); session.commit()
    notices = bank_notices(session, now)
    assert len(notices) == 2
    for n in notices:
        assert_three_parts(n.what, n.why, n.next_step)
        assert n.link == "/financials/bank/"

    # a failure logged by the real sync shows up with the same words
    limited = real_client(pem, answers(429, {"error": "RATE_LIMIT", "message": "x"}))
    run(sync_link(session, limited, soon_link, today=date(2026, 10, 4)))
    texts = [n.text for n in bank_notices(session, now)]
    assert any("4 requests per day" in t for t in texts)


def test_a_stored_failure_without_a_log_entry_still_gives_a_complete_notice(session):
    from app.routers.bank import bank_notices
    conn, link = make_link(session)
    link.last_error = "stored by an earlier version"
    session.add(link); session.commit()
    (notice,) = bank_notices(session)
    assert_three_parts(notice.what, notice.why, notice.next_step)


def test_connections_page_explains_each_state(client, session):
    expired = BankConnection(bank_name="Santander Totta", country="PT", state="p1",
                             status=BankConnectionStatus.EXPIRED)
    waiting = BankConnection(bank_name="Revolut", country="LT", state="p2", status=BankConnectionStatus.PENDING)
    session.add(expired); session.add(waiting); session.commit()
    text = client.get("/financials/bank/").text
    assert "access has expired" in text and "nothing will sync until you renew it" in text
    assert "waiting for you to approve" in text.lower() or "Waiting for you to approve" in text
    assert "press “Try again”" in text


def test_connections_page_explains_unlinked_accounts(client, session):
    conn = BankConnection(bank_name="Santander Totta", country="PT", state="p3", status=BankConnectionStatus.ACTIVE)
    session.add(conn); session.commit(); session.refresh(conn)
    session.add(BankAccountLink(connection_id=conn.id, bank_account_uid="u9", display_name="Conta"))
    session.commit()
    text = client.get("/financials/bank/").text
    assert "aren&#39;t linked" in text or "aren't linked" in text
    assert "Map accounts" in text


@pytest.fixture()
def use_bank(pem):
    """Put a real EnableBankingClient (mock transport) behind the bank routes."""
    from app.main import app
    from app.routers.bank import get_bank_client

    def install(handler):
        app.dependency_overrides[get_bank_client] = lambda: real_client(pem, handler)
    yield install
    app.dependency_overrides.pop(get_bank_client, None)


def test_sync_now_rate_limit_message_reaches_the_page(client, session, use_bank):
    conn, link = make_link(session)
    use_bank(answers(429, {"error": "ASPSP_RATE_LIMIT_EXCEEDED", "message": "slow down"}))
    page = client.post(f"/financials/bank/{conn.id}/sync", follow_redirects=True).text
    assert "4 requests per day" in page and "will try again" in page and "nothing is lost" in page.lower()
    assert "slow down" not in page and "429" not in page


def test_sync_now_with_nothing_linked_says_so(client, session, use_bank):
    conn = BankConnection(bank_name="Santander Totta", country="PT", state="s-un", status=BankConnectionStatus.ACTIVE)
    session.add(conn); session.commit(); session.refresh(conn)
    session.add(BankAccountLink(connection_id=conn.id, bank_account_uid="u9")); session.commit()
    use_bank(OK_NO_ROWS)
    page = client.post(f"/financials/bank/{conn.id}/sync", follow_redirects=True).text
    assert "Nothing was synced." in page and "linked to a Hub account" in page and "Map accounts" in page


def test_sync_now_unexpected_error_is_a_message_and_a_problem_not_a_500(client, session, use_bank, monkeypatch):
    conn, link = make_link(session)
    use_bank(OK_NO_ROWS)

    async def explode(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("app.routers.bank.sync_link", explode)
    r = client.post(f"/financials/bank/{conn.id}/sync", follow_redirects=True)
    assert r.status_code == 200 and "wasn&#39;t updated this time" in r.text
    (entry,) = entries(session, msg.link_ref(link.id), open_only=True)
    assert "kaboom" in entry.technical


def test_connect_failures_say_what_why_and_next_with_the_raw_text_only_in_details(client, session, use_bank):
    use_bank(answers(500, {"error": "INTERNAL", "message": "stack trace here"}))
    page = client.post("/financials/bank/connect/revolut").text
    assert "The bank connection wasn&#39;t started." in page and "tell Pedro" in page
    assert "Back to bank connections" in page
    shown, _, details = page.partition("<details>")
    assert "stack trace here" in details and "stack trace here" not in shown


def test_connect_with_unknown_bank_is_a_message_not_a_crash(client, session, use_bank):
    use_bank(OK_NO_ROWS)
    r = client.post("/financials/bank/connect/nonexistent")
    assert r.status_code == 400 and "isn&#39;t one the Hub can connect to" in r.text


def test_callback_without_finishing_and_with_a_failed_exchange(client, session, use_bank):
    use_bank(answers(500, {"message": "x"}))
    r = client.get("/financials/bank/callback", params={"error": "access_denied"})
    assert "Nothing was connected." in r.text and "cancelled or timed out" in r.text and "Connect or Renew" in r.text

    session.add(BankConnection(bank_name="Revolut", country="LT", state="live-state",
                               status=BankConnectionStatus.PENDING))
    session.commit()
    r = client.get("/financials/bank/callback", params={"state": "live-state", "code": "c"})
    assert "Nothing was saved." in r.text and "Try connecting again" in r.text


def test_picking_a_missing_hub_account_when_mapping_gives_three_parts(client, session, use_bank):
    conn, link = make_link(session)
    use_bank(OK_NO_ROWS)
    r = client.post(f"/financials/bank/{conn.id}/map/{link.id}", data={"account_id": "9999"})
    assert r.status_code == 400
    assert "Not saved." in r.text and "doesn&#39;t exist" in r.text and "Pick one of the listed Hub accounts" in r.text


def test_unknown_bank_connection_and_account_are_complete_404s(client, session, use_bank):
    use_bank(OK_NO_ROWS)
    r = client.get("/financials/bank/999/map", headers={"accept": "text/html"})
    assert r.status_code == 404 and "Couldn&#39;t open that bank connection." in r.text and "What you can do" in r.text


# --- bills ---------------------------------------------------------------------------------------------

def test_bill_that_needs_attention_always_explains_itself(client, session):
    doc = Document(filename="bill.pdf", file_path="/x", content_hash="needs-1", source=DocumentSource.MANUAL,
                   domain=Domain.FINANCIALS, status=DocumentStatus.NEEDS_ATTENTION)
    session.add(doc); session.commit(); session.refresh(doc)
    listing = client.get("/financials/bills").text
    assert "to see why and what to do" in listing
    detail = client.get(f"/financials/bills/{doc.id}").text
    assert "This document needs attention." in detail and "Edit / re-file" in detail


# --- the statement reminder ------------------------------------------------------------------------------

USERS = {111: "Maria", 222: "pedro"}


def _due(session):
    doc = Document(filename="r.pdf", file_path="/tmp/r.pdf", content_hash="rem-1", source=DocumentSource.MANUAL)
    session.add(doc); session.commit(); session.refresh(doc)
    session.add(BalanceSnapshot(as_of=date(2026, 2, 28), kind="deposit", label="D", amount=1.0, document_id=doc.id))
    session.commit()
    return date(2026, 8, 1)


def _send_ok(*a):
    return None


def test_a_reminder_that_cannot_be_delivered_is_on_the_problems_page_with_why_and_what_to_do(session):
    today = _due(session)
    cases = [
        dict(token="", users=USERS, name="Pedro"),
        dict(token="T", users=USERS, name="Nobody"),
        dict(token="T", users={}, name="Pedro", users_error="malformed entry 'x:y:z'"),
    ]
    expected = ["Telegram bot", "Nobody", "can't be read"]
    for kw, wanted in zip(cases, expected):
        assert reminder_job.run(session, today, send=_send_ok, **kw) is False
        # one entry for "the reminder isn't going out", always showing the current reason
        (entry,) = entries(session, reminder.PROBLEM_REF, open_only=True)
        assert entry.area == "Reminders" and wanted in entry.why
        assert_entry_is_complete(entry)
    assert entry.times == 3


def test_a_telegram_error_is_logged_with_its_text_as_technical_detail_then_cleared_by_a_send(session):
    today = _due(session)

    def telegram_down(token, chat_id, text):
        raise ConnectionError("telegram down")

    assert reminder_job.run(session, today, send=telegram_down, token="T", users=USERS, name="Pedro") is False
    (entry,) = entries(session, reminder.PROBLEM_REF, open_only=True)
    assert "ConnectionError: telegram down" in entry.technical and "telegram down" not in entry.why
    assert_entry_is_complete(entry)
    assert reminder_to_send_still(session, today)

    assert reminder_job.run(session, today, send=_send_ok, token="T", users=USERS, name="Pedro") is True
    assert entries(session, reminder.PROBLEM_REF, open_only=True) == []


def reminder_to_send_still(session, today):
    return reminder.reminder_to_send(session, today)


def test_a_reminder_problem_clears_when_nothing_is_due_any_more(session):
    today = _due(session)
    reminder_job.run(session, today, send=_send_ok, token="", users=USERS, name="Pedro")
    assert entries(session, reminder.PROBLEM_REF, open_only=True)
    reminder_job.run(session, date(2026, 3, 1), send=_send_ok, token="", users=USERS, name="Pedro")   # not due
    assert entries(session, reminder.PROBLEM_REF, open_only=True) == []
