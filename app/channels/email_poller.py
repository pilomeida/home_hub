"""Email ingestion channel: polls the Hub's dedicated mailbox over IMAP and
drops every attachment from an allow-listed sender into the Inbox.

Run once per systemd timer tick (deploy/systemd/home-hub-mailpoll.timer):
    python -m app.channels.email_poller
Handled mail is MOVED out of INBOX into Hub-Processed / Hub-Ignored /
Hub-Failed -- never deleted, and never tracked via read/unread flags."""

from __future__ import annotations

import asyncio
import imaplib
import logging
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

from sqlmodel import Session

from app.channels.email_parsing import ParsedEmail, parse_email
from app.config import settings
from app.models.document import DocumentSource
from app.services.inbox_service import receive_document
from app.services.ingestion import IncomingFile

logger = logging.getLogger("home_hub.email_poller")

PROCESSED_FOLDER = "Hub-Processed"
IGNORED_FOLDER = "Hub-Ignored"
FAILED_FOLDER = "Hub-Failed"
MAX_MESSAGES_PER_RUN = 20


class Mailbox(Protocol):
    def ensure_folders(self, names: Iterable[str]) -> None: ...
    def list_uids(self) -> list[bytes]: ...
    def fetch(self, uid: bytes) -> bytes: ...
    def move(self, uid: bytes, folder: str) -> None: ...
    def close(self) -> None: ...


class ImapMailbox:
    """Thin imaplib wrapper. Only INBOX is read."""

    def __init__(self, host: str, port: int, user: str, password: str):
        self._imap = imaplib.IMAP4_SSL(host, port)
        self._imap.login(user, password)
        _, caps = self._imap.capability()
        self._can_move = b"MOVE" in (caps[0] or b"").upper().split()
        self._imap.select("INBOX")

    def ensure_folders(self, names: Iterable[str]) -> None:
        for name in names:
            self._imap.create(name)  # answers NO if it already exists -- harmless

    def list_uids(self) -> list[bytes]:
        _, data = self._imap.uid("SEARCH", None, "ALL")
        return (data[0] or b"").split()

    def fetch(self, uid: bytes) -> bytes:
        _, data = self._imap.uid("FETCH", uid, "(BODY.PEEK[])")
        return data[0][1]

    def move(self, uid: bytes, folder: str) -> None:
        if self._can_move:
            self._imap.uid("MOVE", uid, folder)
            return
        self._imap.uid("COPY", uid, folder)
        self._imap.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
        self._imap.expunge()

    def close(self) -> None:
        try:
            self._imap.logout()
        except Exception:
            pass


@dataclass
class PollReport:
    processed: int = 0
    ignored: int = 0
    failed: int = 0
    documents_created: int = 0


def _family_submitter(parsed: ParsedEmail, allowed_senders: frozenset[str]) -> str | None:
    """The allow-listed account that sent this mail in: the sender itself, or
    (for a Gmail auto-forward, which keeps the original From) the forwarder."""
    for candidate in (parsed.sender, parsed.forwarded_by):
        if candidate and candidate in allowed_senders:
            return candidate
    return None


async def poll_once(
    mailbox: Mailbox,
    session_factory: Callable[[], Session],
    allowed_senders: frozenset[str],
    receive=receive_document,
) -> PollReport:
    report = PollReport()
    mailbox.ensure_folders([PROCESSED_FOLDER, IGNORED_FOLDER, FAILED_FOLDER])

    for uid in mailbox.list_uids()[:MAX_MESSAGES_PER_RUN]:
        try:
            parsed = parse_email(mailbox.fetch(uid))
        except Exception:
            logger.exception("could not read message uid=%s", uid)
            mailbox.move(uid, FAILED_FOLDER)
            report.failed += 1
            continue

        submitter = _family_submitter(parsed, allowed_senders)
        if submitter is None:
            logger.info("ignoring mail from non-allow-listed sender %s (forwarded by %r)",
                        parsed.sender, parsed.forwarded_by)
            mailbox.move(uid, IGNORED_FOLDER)
            report.ignored += 1
            continue
        if not parsed.attachments:
            logger.info("ignoring mail without usable attachments from %s: %s", parsed.sender, parsed.subject)
            mailbox.move(uid, IGNORED_FOLDER)
            report.ignored += 1
            continue

        try:
            with session_factory() as session:
                for index, attachment in enumerate(parsed.attachments):
                    receipt = await receive(
                        session,
                        IncomingFile(filename=attachment.filename, content=attachment.content,
                                     source=DocumentSource.EMAIL, uploaded_by=submitter),
                        context_text=parsed.context_text,
                        external_ref=f"email:{parsed.message_id}#{index}",
                    )
                    if not receipt.duplicate:
                        report.documents_created += 1
        except Exception:
            # Content-hash dedup makes a later retry (moving the mail back to
            # INBOX) safe even if some attachments were already stored.
            logger.exception("failed to ingest mail uid=%s from %s", uid, parsed.sender)
            mailbox.move(uid, FAILED_FOLDER)
            report.failed += 1
            continue

        mailbox.move(uid, PROCESSED_FOLDER)
        report.processed += 1

    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not settings.imap_configured:
        print("Hub mailbox not configured (HUB_IMAP_* unset) — nothing to do.")
        return 0

    from app.db import engine

    mailbox = ImapMailbox(settings.HUB_IMAP_HOST, settings.HUB_IMAP_PORT,
                          settings.HUB_IMAP_USER, settings.HUB_IMAP_PASSWORD)
    try:
        report = asyncio.run(poll_once(mailbox, lambda: Session(engine), settings.imap_allowed_senders))
    finally:
        mailbox.close()
    print(f"mail poll: {report}")
    return 1 if report.failed else 0  # non-zero => unit shows "failed" (surfaced by deploy + runbook)


if __name__ == "__main__":
    sys.exit(main())
