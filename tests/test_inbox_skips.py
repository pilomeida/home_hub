"""Nothing that arrives is dropped silently: decorative images (logos, banners, signatures) are not added, exact
copies of documents already held are not added twice, and the Inbox page says which and why."""

import struct
import zlib
from email.message import EmailMessage

import pytest
from sqlmodel import Session, select

from app.channels.email_parsing import image_size, parse_email
from app.channels.email_poller import FAILED_FOLDER, IGNORED_FOLDER, PROCESSED_FOLDER, PollReport, poll_once
from app.jobs import runners
from app.models.document import DocumentSource
from app.models.domain import Domain
from app.models.inbox_item import InboxSkip
from app.services import inbox_service
from app.services.domain_classifier import DomainSuggestion
from app.services.ingestion import IncomingFile
from tests.test_email_poller import ALLOWED, FakeMailbox


def png(width, height, pad_to=0):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    body = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    return body + b"\0" * max(0, pad_to - len(body))


def jpeg(width, height, pad_to=0):
    body = b"\xff\xd8" + b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 3) + b"\0\0\0\0\0\0"
    return body + b"\0" * max(0, pad_to - len(body))


def mail(*parts, inline=()):
    """An email with a body, the given (filename, bytes, subtype) attachments, and inline images (cid)."""
    msg = EmailMessage()
    msg["From"] = "pedro@example.com"
    msg["Subject"] = "Condições gerais"
    msg["Message-ID"] = "<m@x>"
    msg.set_content("see attached")
    msg.add_alternative("<html><body><p>hi</p></body></html>", subtype="html")
    for name, content, subtype in inline:
        msg.get_payload()[1].add_related(content, "image", subtype, cid=f"<{name}>", filename=name, disposition="inline")
    for name, content, subtype in parts:
        maintype = "application" if subtype == "pdf" else "image"
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=name)
    return msg.as_bytes()


def kept_and_skipped(raw):
    parsed = parse_email(raw)
    return [a.filename for a in parsed.attachments], [(s.filename, s.detail) for s in parsed.skipped]


def test_the_banner_from_the_real_insurance_email_is_dropped_and_the_pdfs_are_kept():
    raw = mail(("CPAP3000310040.pdf", b"%PDF-1.4 " + b"x" * 5000, "pdf"),
               inline=[("image001.png", png(640, 300, pad_to=29_991), "png")])        # 29,991 bytes, embedded: the real one
    kept, skipped = kept_and_skipped(raw)
    assert kept == ["CPAP3000310040.pdf"]
    assert skipped and skipped[0][0] == "image001.png" and "embedded in the email body" in skipped[0][1]


def test_a_real_photo_of_a_document_is_kept_even_when_embedded_or_generically_named():
    photo = jpeg(3000, 4000, pad_to=400_000)
    assert kept_and_skipped(mail(("photo.jpg", photo, "jpeg"))) == (["photo.jpg"], [])
    assert kept_and_skipped(mail(inline=[("image.jpg", photo, "jpeg")])) == (["image.jpg"], [])


def test_banner_shaped_tiny_and_logo_named_images_are_dropped():
    strip = png(1000, 150, pad_to=200_000)                                           # a banner strip
    tiny = png(100, 100, pad_to=40_000)                                              # a tiny icon
    logo = png(900, 700, pad_to=60_000)                                              # small file named like a logo
    for name, content in (("promo.png", strip), ("icon.png", tiny), ("company-logo.png", logo)):
        kept, skipped = kept_and_skipped(mail((name, content, "png")))
        assert kept == [] and [s[0] for s in skipped] == [name], name
    big = png(900, 700, pad_to=300_000)
    assert kept_and_skipped(mail(("company-logo.png", big, "png")))[0] == ["company-logo.png"]   # big enough to be a document


def test_image_sizes_are_read_from_headers():
    assert image_size(png(600, 330)) == (600, 330) and image_size(jpeg(1200, 800)) == (1200, 800)
    assert image_size(b"GIF89a\x10\x00\x20\x00") == (16, 32) and image_size(b"not an image") is None


@pytest.mark.asyncio
async def test_the_poller_records_decorative_images_and_counts_them(engine):
    raw = mail(("a.pdf", b"%PDF-1.4 " + b"x" * 5000, "pdf"), inline=[("image001.png", png(640, 300, pad_to=30_000), "png")])
    seen = []

    async def receive(session, incoming, **kw):
        seen.append(incoming.filename)
        from app.services.inbox_service import InboxReceipt
        return InboxReceipt(document=None, inbox_item=None, duplicate=False)

    box = FakeMailbox({b"1": raw})
    report = await poll_once(box, lambda: Session(engine), frozenset({"pedro@example.com"}), receive=receive)
    assert seen == ["a.pdf"] and report.decorative == 1 and report.documents_created == 1
    with Session(engine) as s:
        skip = s.exec(select(InboxSkip)).one()
        assert (skip.filename, skip.source, skip.reason) == ("image001.png", "email", "decorative")


@pytest.mark.usefixtures("two_domains", "inbox_documents_dir")
@pytest.mark.asyncio
async def test_an_exact_copy_of_a_held_document_is_recorded_with_where_it_already_is(session):
    async def classify(file_path, context_text=None):
        return DomainSuggestion(Domain.HOUSE, "manual", 0.95, "a manual")

    async def send(name):
        return await inbox_service.receive_document(
            session, IncomingFile(name, b"%PDF same bytes", DocumentSource.EMAIL, "pedro"),
            context_text=None, external_ref="email:<m>#0", classify=classify)
    first = await send("apolice_resumo.pdf")
    second = await send("OK_02_copy.pdf")
    assert second.duplicate and second.document.id == first.document.id
    skip = session.exec(select(InboxSkip)).one()
    assert skip.reason == "duplicate" and skip.filename == "OK_02_copy.pdf" and skip.document_id == first.document.id
    assert "“apolice_resumo.pdf”" in skip.detail and "waiting in the Inbox" in skip.detail
    assert len(session.exec(select(InboxSkip)).all()) == 1


@pytest.mark.usefixtures("two_domains", "inbox_documents_dir")
def test_the_inbox_page_lists_what_was_not_added_and_why(client, session):
    inbox_service.record_skip(session, filename="image001.png", source="email", reason="decorative",
                              detail="Not added: a small image embedded in the email body (logo, banner or signature), not a document.")
    page = client.get("/inbox").text
    assert "Not added" in page and "image001.png" in page and "embedded in the email body" in page


def _mailbox_runner(monkeypatch, report):
    from app.config import settings
    monkeypatch.setattr(settings, "HUB_IMAP_HOST", "imap.example.com")
    monkeypatch.setattr(settings, "HUB_IMAP_USER", "u")
    monkeypatch.setattr(settings, "HUB_IMAP_PASSWORD", "p")
    monkeypatch.setattr(runners, "_poll_blocking", lambda bind: report)


@pytest.mark.asyncio
async def test_the_mailbox_job_summary_says_what_was_not_added(session, monkeypatch):
    _mailbox_runner(monkeypatch, PollReport(processed=2, documents_created=5, duplicates=3, decorative=1))
    out = await runners.run_mailbox_check(session, "schedule")
    assert out.outcome == "ok"
    assert out.summary.startswith("Done: 5 documents filed in the Inbox from 2 emails.")
    assert "Not added: 3 attachments were exact copies of documents already in the Hub and 1 image was a logo or banner" in out.summary
    assert "Not added" in out.summary and "Inbox page" in out.summary
