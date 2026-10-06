"""The 5-monthly statement reminder: pure date logic, the once-per-cycle log,
and the Telegram job with an injected sender. All data is synthetic."""
from datetime import date

import pytest
from sqlmodel import select

from app.jobs import statement_reminder as job
from app.models.document import Document, DocumentSource
from app.models.position import BalanceSnapshot, LoanMovement, ReminderLog
from app.services.statement_reminder import (
    REMINDER_TEXT, get_statement_reminder, mark_reminder_sent, reminder_to_send,
)
from app.models.debt import Debt, DebtDirection, DebtKind


def _doc(session, n=1):
    d = Document(filename=f"r{n}.pdf", file_path=f"/tmp/r{n}.pdf", content_hash=f"reminder-{n}",
                 source=DocumentSource.MANUAL)
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def _snapshot(session, as_of, n=1):
    session.add(BalanceSnapshot(as_of=as_of, kind="deposit", label="D", amount=1.0, document_id=_doc(session, n).id))
    session.commit()


def test_reminder_text_is_exact():
    assert REMINDER_TEXT == ("Reminder: to keep loan and savings data updated, you have 30 days to download "
                             "the latest digital bank statements from Santander and share them with me.")


def test_no_data_is_not_due(session):
    state = get_statement_reminder(session, date(2030, 1, 1))
    assert (state.due, state.overdue, state.cycle_start, state.deadline, state.last_positions) == \
        (False, False, None, None, None)
    assert reminder_to_send(session, date(2030, 1, 1)) is False


def test_cycle_start_deadline_and_overdue(session):
    _snapshot(session, date(2026, 2, 28))
    before = get_statement_reminder(session, date(2026, 7, 27))
    assert before.due is False and before.cycle_start == date(2026, 7, 28)
    day = get_statement_reminder(session, date(2026, 7, 28))
    assert day.due is True and day.overdue is False
    assert day.deadline == date(2026, 8, 27) and day.last_positions == date(2026, 2, 28)
    assert get_statement_reminder(session, date(2026, 8, 27)).overdue is False
    assert get_statement_reminder(session, date(2026, 8, 28)).overdue is True


@pytest.mark.parametrize("last,start", [(date(2026, 8, 31), date(2027, 1, 31)),
                                        (date(2026, 9, 30), date(2027, 2, 28)),
                                        (date(2026, 10, 31), date(2027, 3, 31))])
def test_month_end_clamping(session, last, start):
    _snapshot(session, last)
    assert get_statement_reminder(session, date(2030, 1, 1)).cycle_start == start


def test_newest_snapshot_wins_and_loan_movements_never_postpone_the_reminder(session):
    _snapshot(session, date(2026, 1, 15))
    debt = Debt(kind=DebtKind.FORMAL, direction=DebtDirection.OWED_BY_US, original_amount=1.0,
                current_balance=1, name="L", external_number="900100200", loan_type="mortgage")
    session.add(debt)
    session.commit()
    session.refresh(debt)
    session.add(LoanMovement(debt_id=debt.id, instalment_number=1, movement_date=date(2026, 5, 10),
                             capital=1.0, interest=1.0, document_id=_doc(session, 2).id))
    session.commit()
    state = get_statement_reminder(session, date(2026, 6, 14))
    # a printout upload (movements) must not postpone the statement reminder
    assert state.last_positions == date(2026, 1, 15) and state.cycle_start == date(2026, 6, 15)
    assert state.due is False
    assert get_statement_reminder(session, date(2026, 6, 15)).due is True


def test_newer_upload_clears_due(session):
    _snapshot(session, date(2026, 2, 28))
    assert get_statement_reminder(session, date(2026, 8, 1)).due is True
    _snapshot(session, date(2026, 7, 31), n=2)
    assert get_statement_reminder(session, date(2026, 8, 1)).due is False


def test_reminder_sent_once_per_cycle(session):
    _snapshot(session, date(2026, 2, 28))
    today = date(2026, 8, 1)
    assert reminder_to_send(session, today) is True
    mark_reminder_sent(session, today)
    row = session.exec(select(ReminderLog)).one()
    assert row.key == "statement_positions" and row.cycle_start == date(2026, 7, 28)
    assert reminder_to_send(session, today) is False
    assert reminder_to_send(session, date(2026, 8, 20)) is False
    _snapshot(session, date(2026, 8, 31), n=2)  # next cycle starts 2027-01-31
    assert reminder_to_send(session, date(2027, 1, 31)) is True


def test_not_due_is_never_sent(session):
    _snapshot(session, date(2026, 7, 31))
    assert reminder_to_send(session, date(2026, 8, 1)) is False


# ---- the Telegram job --------------------------------------------------

USERS = {111: "Maria", 222: "pedro"}


def _due(session):
    _snapshot(session, date(2026, 2, 28))
    return date(2026, 8, 1)


def test_job_sends_exact_text_to_the_named_user_and_marks_sent(session):
    today = _due(session)
    calls = []
    sent = job.run(session, today, send=lambda t, c, x: calls.append((t, c, x)),
                   token="TEST-TOKEN", users=USERS, name="Pedro")
    assert sent is True and calls == [("TEST-TOKEN", 222, REMINDER_TEXT)]
    assert reminder_to_send(session, today) is False
    again = job.run(session, today, send=lambda *a: calls.append(a), token="TEST-TOKEN", users=USERS, name="Pedro")
    assert again is False and len(calls) == 1


def test_job_unknown_name_sends_nothing(session, caplog):
    today = _due(session)
    calls = []
    assert job.run(session, today, send=lambda *a: calls.append(a), token="T", users=USERS, name="Nobody") is False
    assert calls == [] and reminder_to_send(session, today) is True
    assert "recipient" in caplog.text.lower()


def test_job_without_token_sends_nothing(session):
    today = _due(session)
    calls = []
    assert job.run(session, today, send=lambda *a: calls.append(a), token="", users=USERS, name="Pedro") is False
    assert calls == [] and reminder_to_send(session, today) is True


def test_job_not_due_sends_nothing(session):
    _snapshot(session, date(2026, 7, 31))
    calls = []
    assert job.run(session, date(2026, 8, 1), send=lambda *a: calls.append(a), token="T", users=USERS, name="Pedro") is False
    assert calls == []


def test_job_sender_failure_is_logged_not_marked_not_raised(session, caplog):
    today = _due(session)

    def boom(*a):
        raise RuntimeError("telegram down")

    assert job.run(session, today, send=boom, token="T", users=USERS, name="Pedro") is False
    assert reminder_to_send(session, today) is True
    assert "telegram down" in caplog.text


def test_job_main_exits_zero_without_token(monkeypatch):
    monkeypatch.setattr(job.settings, "HUB_TELEGRAM_BOT_TOKEN", "")
    assert job.main() == 0


def test_job_main_with_malformed_allowed_users_logs_and_exits_zero(monkeypatch, caplog):
    monkeypatch.setattr(job.settings, "HUB_TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("HUB_TELEGRAM_ALLOWED_USERS", "this-is-not:valid:format")
    assert job.main() == 0
    assert "HUB_TELEGRAM_ALLOWED_USERS" in caplog.text
