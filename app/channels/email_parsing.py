"""Pure parsing of one raw email into the attachments the Hub should ingest."""

from __future__ import annotations

import email
import email.policy
from dataclasses import dataclass, field
from email.utils import parseaddr

from app.domains.base import MediaKind
from app.domains.fields import media_kind_for

MIN_IMAGE_BYTES = 15_000            # smaller images are signature logos / tracking pixels
MAX_FILE_BYTES = 25 * 1024 * 1024
_CONTEXT_BODY_CHARS = 500


@dataclass
class EmailAttachment:
    filename: str
    content: bytes


@dataclass
class ParsedEmail:
    sender: str
    subject: str
    message_id: str
    context_text: str
    attachments: list[EmailAttachment] = field(default_factory=list)
    # The Gmail account whose forwarding rule sent this mail on ("" if none).
    # Auto-forwarding keeps the bill's own From, so the allowlist checks this too.
    forwarded_by: str = ""


def _forwarded_by(msg) -> str:
    # Gmail stamps auto-forwarded mail "X-Forwarded-For: <forwarder> <destination>".
    first = str(msg.get("X-Forwarded-For", "")).split()[:1]
    return parseaddr(first[0])[1].strip().lower() if first else ""


def parse_email(raw: bytes) -> ParsedEmail:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = parseaddr(str(msg.get("From", "")))[1].strip().lower()
    subject = str(msg.get("Subject", "")).strip()
    message_id = str(msg.get("Message-ID", "")).strip()
    forwarded_by = _forwarded_by(msg)

    body_part = msg.get_body(preferencelist=("plain",))
    body = body_part.get_content().strip() if body_part is not None else ""
    context_text = f"Email subject: {subject}\n{body[:_CONTEXT_BODY_CHARS]}".strip()
    if forwarded_by:
        context_text = f"Originally from: {sender}\n{context_text}"

    attachments: list[EmailAttachment] = []
    for part in msg.walk():  # also descends into forwarded message/rfc822 parts
        if part.is_multipart():
            continue
        filename = part.get_filename()
        if not filename:
            continue
        kind = media_kind_for(filename)
        if kind == MediaKind.OTHER:
            continue
        content = part.get_payload(decode=True) or b""
        if len(content) > MAX_FILE_BYTES:
            continue
        if kind == MediaKind.IMAGE and len(content) < MIN_IMAGE_BYTES:
            continue
        attachments.append(EmailAttachment(filename=filename, content=content))

    return ParsedEmail(sender=sender, subject=subject, message_id=message_id,
                       context_text=context_text, attachments=attachments,
                       forwarded_by=forwarded_by)
