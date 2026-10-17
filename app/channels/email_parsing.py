"""Pure parsing of one raw email into the attachments the Hub should ingest."""

from __future__ import annotations

import email
import email.policy
import re
import struct
from dataclasses import dataclass, field
from email.utils import parseaddr
from typing import Optional

from app.domains.base import MediaKind
from app.domains.fields import media_kind_for

MIN_IMAGE_BYTES = 15_000            # smaller images are signature logos / tracking pixels
# Decorative images (Pedro, 2026-10-17: logos, banners, signature images are dropped altogether). Decided without AI,
# from how the image sits in the email, its name and its size; a real photo of a document is large and is kept.
_LOGO_NAME = re.compile(
    r"logo|banner|signature|assinatura|facebook|linkedin|instagram|twitter|youtube|whatsapp|spacer|pixel", re.IGNORECASE)
_INLINE_SMALL_BYTES = 150_000
_TINY_PIXELS = 60_000               # e.g. 240 x 250
_SMALL_PIXELS = 200_000             # e.g. 600 x 330
_STRIP_RATIO = 5                    # a banner strip: five times wider than tall (or the reverse)
MAX_FILE_BYTES = 25 * 1024 * 1024
_CONTEXT_BODY_CHARS = 500


@dataclass
class EmailAttachment:
    filename: str
    content: bytes


@dataclass
class SkippedAttachment:
    filename: str
    detail: str                     # why it was not taken, in plain words (shown on the Inbox page)


@dataclass
class ParsedEmail:
    sender: str
    subject: str
    message_id: str
    context_text: str
    attachments: list[EmailAttachment] = field(default_factory=list)
    skipped: list[SkippedAttachment] = field(default_factory=list)    # decorative images, with the reason
    # The Gmail account whose forwarding rule sent this mail on ("" if none).
    # Auto-forwarding keeps the bill's own From, so the allowlist checks this too.
    forwarded_by: str = ""


def _forwarded_by(msg) -> str:
    # Gmail stamps auto-forwarded mail "X-Forwarded-For: <forwarder> <destination>".
    first = str(msg.get("X-Forwarded-For", "")).split()[:1]
    return parseaddr(first[0])[1].strip().lower() if first else ""


def image_size(content: bytes) -> Optional[tuple[int, int]]:
    """(width, height) of a PNG, GIF or JPEG, read from its header; None when it cannot be told."""
    try:
        if content[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", content[16:24])
        if content[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", content[6:10])
        if content[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(content):
                if content[i] != 0xFF:
                    i += 1
                    continue
                marker = content[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", content[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    height, width = struct.unpack(">HH", content[i + 5:i + 9])
                    return width, height
                i += 2 + length
    except (struct.error, IndexError):
        return None
    return None


def decorative_reason(part, filename: str, content: bytes) -> Optional[str]:
    """Why this image is a logo, banner or signature rather than a document ("" never); None when it may be a document."""
    size = image_size(content)
    pixels = size[0] * size[1] if size else None
    inline = (part.get_content_disposition() == "inline") or bool(part.get("Content-ID"))
    logo_named = bool(_LOGO_NAME.search(filename or ""))
    if pixels is not None and pixels < _TINY_PIXELS:
        return f"a very small image ({size[0]}×{size[1]} px): a logo, icon or tracking pixel, not a document"
    if size and max(size) / max(1, min(size)) >= _STRIP_RATIO and max(size) <= 1200:
        return f"a banner-shaped image ({size[0]}×{size[1]} px), not a document"
    if (inline or logo_named) and (len(content) < _INLINE_SMALL_BYTES or (pixels is not None and pixels < _SMALL_PIXELS)):
        what = "embedded in the email body" if inline else "named like a logo or signature image"
        return f"a small image {what} (logo, banner or signature), not a document"
    return None


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
    skipped: list[SkippedAttachment] = []
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
        if kind == MediaKind.IMAGE:
            reason = ("a tiny image (a logo or tracking pixel), not a document" if len(content) < MIN_IMAGE_BYTES
                      else decorative_reason(part, filename, content))
            if reason:
                skipped.append(SkippedAttachment(filename=filename, detail=f"Not added: {reason}."))
                continue
        attachments.append(EmailAttachment(filename=filename, content=content))

    return ParsedEmail(sender=sender, subject=subject, message_id=message_id,
                       context_text=context_text, attachments=attachments, skipped=skipped,
                       forwarded_by=forwarded_by)
