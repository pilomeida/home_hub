"""Telegram ingestion channel: the Hub's own bot (NOT the Recipes bot).
Same python-telegram-bot polling pattern as Recipes/app/bot.py, but run as
its own systemd service (deploy/systemd/home-hub-telegram.service):
    python -m app.channels.telegram_bot
Every file from an allow-listed family member goes to the Inbox via
inbox_service.receive_document. Messages from anyone else are ignored
silently (the bot is publicly findable); their id is logged so the operator
can add a family member."""

from __future__ import annotations

import logging
import mimetypes
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from sqlmodel import Session
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import settings
from app.domains.base import MediaKind
from app.domains.fields import media_kind_for
from app.models.document import DocumentSource, DocumentStatus
from app.services.inbox_service import InboxReceipt, describe, is_confident, receive_document, suggested_domain
from app.services.ingestion import IncomingFile

logger = logging.getLogger("home_hub.telegram_bot")

MAX_BOT_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Bot API getFile limit
HELP_TEXT = "Send me a PDF, a photo or a short video and I'll put it in the Hub Inbox for approval."


@dataclass
class PickedFile:
    file_id: str
    filename: str
    size: Optional[int]


def _ext(mime_type: Optional[str], default: str) -> str:
    return (mimetypes.guess_extension(mime_type) or default) if mime_type else default


def pick_file(message) -> Optional[PickedFile]:
    if message.document is not None:
        d = message.document
        return PickedFile(d.file_id, d.file_name or f"telegram-file-{d.file_unique_id}{_ext(d.mime_type, '')}", d.file_size)
    if message.photo:
        p = message.photo[-1]  # largest resolution
        return PickedFile(p.file_id, f"telegram-photo-{p.file_unique_id}.jpg", p.file_size)
    if message.video is not None:
        v = message.video
        return PickedFile(v.file_id, v.file_name or f"telegram-video-{v.file_unique_id}{_ext(v.mime_type, '.mp4')}", v.file_size)
    return None


def reply_for(receipt: InboxReceipt, base_url: str) -> str:
    inbox_url = f"{base_url.rstrip('/')}/inbox"
    if receipt.duplicate:
        if receipt.document.status == DocumentStatus.PENDING_REVIEW:
            return f"I already have this one — it's waiting in the Inbox: {inbox_url}"
        return "I already have this one — nothing to do."
    item = receipt.inbox_item
    if item is not None and is_confident(item):
        label = describe(suggested_domain(item), item.suggested_category)
        return f"Got it — looks like {label}. Approve it in the Inbox: {inbox_url}"
    return f"Got it — I'm not sure where this belongs. Please sort it in the Inbox: {inbox_url}"


async def file_to_inbox(
    *,
    sender_name: str,
    filename: str,
    content: bytes,
    caption: Optional[str],
    message_ref: str,
    session_factory: Callable[[], Session],
    base_url: str,
    receive=receive_document,
) -> str:
    with session_factory() as session:
        receipt = await receive(
            session,
            IncomingFile(filename=filename, content=content, source=DocumentSource.TELEGRAM,
                         uploaded_by=f"{sender_name} (Telegram)"),
            context_text=f"Telegram caption: {caption}" if caption else None,
            external_ref=message_ref,
        )
        return reply_for(receipt, base_url)


def _sender_name(update: Update) -> Optional[str]:
    user = update.effective_user
    name = settings.telegram_allowed_users.get(user.id) if user else None
    if name is None and user is not None:
        logger.info("ignoring message from unknown Telegram user id=%s name=%s", user.id, user.full_name)
    return name


async def on_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.db import engine

    message = update.effective_message
    sender = _sender_name(update)
    if sender is None or message is None:
        return
    picked = pick_file(message)
    if picked is None or media_kind_for(picked.filename) == MediaKind.OTHER:
        await message.reply_text(HELP_TEXT)
        return
    if picked.size and picked.size > MAX_BOT_DOWNLOAD_BYTES:
        await message.reply_text(
            "That file is over 20 MB — Telegram won't let me download it. Please email it to the Hub instead."
        )
        return
    try:
        tg_file = await context.bot.get_file(picked.file_id)
        content = bytes(await tg_file.download_as_bytearray())
        reply = await file_to_inbox(
            sender_name=sender, filename=picked.filename, content=content, caption=message.caption,
            message_ref=f"telegram:{message.chat_id}:{message.message_id}",
            session_factory=lambda: Session(engine), base_url=settings.PUBLIC_BASE_URL,
        )
    except Exception:
        logger.exception("failed to file Telegram message %s from %s", message.message_id, sender)
        reply = "Something went wrong saving that — please try again, or email it to the Hub."
    await message.reply_text(reply)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _sender_name(update) is None or update.effective_message is None:
        return
    await update.effective_message.reply_text(HELP_TEXT)


def build_application(token: str) -> Application:
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", on_text))
    application.add_handler(MessageHandler(
        (filters.Document.ALL | filters.PHOTO | filters.VIDEO) & ~filters.COMMAND, on_file
    ))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return application


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx logs every request URL at INFO -- and Bot API URLs contain the token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not settings.HUB_TELEGRAM_BOT_TOKEN:
        print("Hub Telegram bot not configured (HUB_TELEGRAM_BOT_TOKEN unset) — exiting.")
        return 0
    settings.telegram_allowed_users  # fail fast on a malformed allowlist
    build_application(settings.HUB_TELEGRAM_BOT_TOKEN).run_polling(allowed_updates=Update.ALL_TYPES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
