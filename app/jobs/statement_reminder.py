"""Push the statement reminder to Telegram once per cycle, run daily by the
systemd user timer deploy/systemd/home-hub-reminder.timer.
Usage: python -m app.jobs.statement_reminder

Never fails the timer on a delivery problem: no token / no recipient / a
Telegram error is logged, nothing is marked sent, and the exit code is 0."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date
from typing import Callable, Mapping, Optional

from sqlmodel import Session

from app.config import settings
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


def run(session: Session, today: date, *, send: Sender, token: str,
        users: Mapping[int, str], name: str) -> bool:
    """True when the reminder was sent (and marked) in this run."""
    if not reminder_to_send(session, today):
        logger.info("no reminder due")
        return False
    if not token:
        logger.warning("HUB_TELEGRAM_BOT_TOKEN is not set; reminder not sent")
        return False
    chat_id = find_recipient(users, name)
    if chat_id is None:
        logger.warning("no allowed Telegram user named %r; reminder recipient not found, nothing sent", name)
        return False
    try:
        send(token, chat_id, REMINDER_TEXT)
    except Exception:
        logger.exception("sending the statement reminder failed; will retry on the next run")
        return False
    mark_reminder_sent(session, today)
    logger.info("statement reminder sent")
    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    if not settings.HUB_TELEGRAM_BOT_TOKEN:
        logger.warning("HUB_TELEGRAM_BOT_TOKEN is not set; reminder not sent")
        return 0
    try:
        users = settings.telegram_allowed_users
    except ValueError as exc:  # malformed HUB_TELEGRAM_ALLOWED_USERS never fails the timer
        logger.warning("cannot read HUB_TELEGRAM_ALLOWED_USERS (%s); reminder not sent", exc)
        return 0
    from app.db import engine

    with Session(engine) as session:
        run(session, date.today(), send=telegram_send, token=settings.HUB_TELEGRAM_BOT_TOKEN,
            users=users, name=settings.HUB_REMINDER_TELEGRAM_NAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())
