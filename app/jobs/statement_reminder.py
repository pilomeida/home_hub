"""Push the statement reminder to Telegram once per cycle, run daily by the
systemd user timer deploy/systemd/home-hub-reminder.timer.
Usage: python -m app.jobs.statement_reminder

Never fails the timer on a delivery problem: no token / no recipient / a
Telegram error is logged and put on the Problems page, nothing is marked sent, and the exit code is 0."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date
from typing import Callable, Mapping, Optional

from sqlmodel import Session

from app.config import settings
from app.problems import Problem, record_problem, resolve_for
from app.services import statement_reminder as msg
from app.services.statement_reminder import REMINDER_TEXT, mark_reminder_sent, reminder_to_send

logger = logging.getLogger("statement_reminder")

Sender = Callable[[str, int, str], None]


def telegram_send(token: str, chat_id: int, text: str) -> None:
    from telegram import Bot

    async def _go() -> None:
        async with Bot(token) as bot:
            await bot.send_message(chat_id=chat_id, text=text)

    asyncio.run(_go())


def find_recipient(users: Mapping[int, str], name: str) -> Optional[int]:
    wanted = (name or "").strip().casefold()
    for chat_id, user_name in users.items():
        if user_name.strip().casefold() == wanted:
            return chat_id
    return None


def _log_problem(session: Session, problem: Problem, technical: Optional[str] = None) -> None:
    """A reminder that couldn't go out is invisible by nature (nobody is watching a 09:00 timer), so it
    is kept on the Problems page until a later run sends it."""
    try:
        record_problem(session, area=msg.PROBLEM_AREA, problem=problem, ref=msg.PROBLEM_REF, technical=technical)
    except Exception:       # the log must never be the reason the timer fails
        logger.exception("could not record the reminder problem")


def run(session: Session, today: date, *, send: Sender, token: str,
        users: Mapping[int, str], name: str, users_error: Optional[str] = None) -> bool:
    """True when the reminder was sent (and marked) in this run."""
    if not reminder_to_send(session, today):
        logger.info("no reminder due")
        resolve_for(session, msg.PROBLEM_REF)      # nothing is waiting to be sent any more
        return False
    if not token:
        logger.warning("HUB_TELEGRAM_BOT_TOKEN is not set; reminder not sent")
        _log_problem(session, msg.problem_no_token(), "HUB_TELEGRAM_BOT_TOKEN is not set")
        return False
    if users_error:
        logger.warning("cannot read HUB_TELEGRAM_ALLOWED_USERS (%s); reminder not sent", users_error)
        _log_problem(session, msg.problem_bad_user_list(), f"HUB_TELEGRAM_ALLOWED_USERS: {users_error}")
        return False
    chat_id = find_recipient(users, name)
    if chat_id is None:
        logger.warning("no allowed Telegram user named %r; reminder recipient not found, nothing sent", name)
        _log_problem(session, msg.problem_no_recipient(name),
                     f"HUB_REMINDER_TELEGRAM_NAME={name!r}; allowed users: {sorted(users.values())}")
        return False
    try:
        send(token, chat_id, REMINDER_TEXT)
    except Exception as exc:
        logger.exception("sending the statement reminder failed; will retry on the next run")
        _log_problem(session, msg.problem_send_failed(), f"{type(exc).__name__}: {exc}")
        return False
    mark_reminder_sent(session, today)
    resolve_for(session, msg.PROBLEM_REF)
    logger.info("statement reminder sent")
    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    users: Mapping[int, str] = {}
    users_error: Optional[str] = None
    try:
        users = settings.telegram_allowed_users
    except ValueError as exc:  # malformed HUB_TELEGRAM_ALLOWED_USERS never fails the timer
        users_error = str(exc)
        logger.warning("cannot read HUB_TELEGRAM_ALLOWED_USERS (%s)", users_error)
    from app.db import engine

    try:
        with Session(engine) as session:
            run(session, date.today(), send=telegram_send, token=settings.HUB_TELEGRAM_BOT_TOKEN,
                users=users, name=settings.HUB_REMINDER_TELEGRAM_NAME, users_error=users_error)
    except Exception:       # the timer must not fail even if the database can't be reached
        logger.exception("the statement reminder check could not run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
