from types import SimpleNamespace

import pytest
from sqlmodel import Session

from app.channels import telegram_bot
from app.channels.telegram_bot import PickedFile, file_to_inbox, pick_file, reply_for
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.inbox_item import InboxItem
from app.services.inbox_service import InboxReceipt

pytestmark = pytest.mark.usefixtures("two_domains")


def _msg(**kw):
    base = dict(document=None, photo=None, video=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_pick_file_document():
    msg = _msg(document=SimpleNamespace(file_id="F1", file_unique_id="U1", file_name="manual.pdf",
                                        file_size=1000, mime_type="application/pdf"))
    assert pick_file(msg) == PickedFile("F1", "manual.pdf", 1000)


def test_pick_file_document_without_name_uses_mime():
    msg = _msg(document=SimpleNamespace(file_id="F1", file_unique_id="U1", file_name=None,
                                        file_size=10, mime_type="application/pdf"))
    assert pick_file(msg).filename == "telegram-file-U1.pdf"


def test_pick_file_largest_photo_and_video():
    small = SimpleNamespace(file_id="S", file_unique_id="US", file_size=10)
    large = SimpleNamespace(file_id="L", file_unique_id="UL", file_size=99)
    assert pick_file(_msg(photo=[small, large])) == PickedFile("L", "telegram-photo-UL.jpg", 99)
    video = SimpleNamespace(file_id="V", file_unique_id="UV", file_name=None, file_size=5, mime_type="video/mp4")
    assert pick_file(_msg(video=video)) == PickedFile("V", "telegram-video-UV.mp4", 5)
    assert pick_file(_msg()) is None


def _receipt(confident=True, duplicate=False, status=DocumentStatus.PENDING_REVIEW):
    doc = Document(id=1, filename="x.pdf", file_path="/x", content_hash="h",
                   source=DocumentSource.TELEGRAM, status=status)
    item = InboxItem(document_id=1, suggested_domain="house", suggested_category="manual",
                     confidence=0.95 if confident else 0.2)
    return InboxReceipt(document=doc, inbox_item=item, duplicate=duplicate)


def test_replies():
    assert "Fake › Manual" in reply_for(_receipt(), "https://hub.example")
    assert "https://hub.example/inbox" in reply_for(_receipt(), "https://hub.example")
    assert "not sure" in reply_for(_receipt(confident=False), "https://h")
    assert "waiting in the Inbox" in reply_for(_receipt(duplicate=True), "https://h")
    filed = reply_for(_receipt(duplicate=True, status=DocumentStatus.PROCESSED), "https://h")
    assert "already have" in filed and "waiting" not in filed


@pytest.mark.asyncio
async def test_file_to_inbox_passes_channel_details(engine):
    calls = []

    async def fake_receive(session, incoming, **kwargs):
        calls.append((incoming, kwargs))
        return _receipt()

    text = await file_to_inbox(
        sender_name="Rute", filename="a.pdf", content=b"%PDF", caption="boiler warranty",
        message_ref="telegram:5:6", session_factory=lambda: Session(engine),
        base_url="https://h", receive=fake_receive,
    )

    incoming, kwargs = calls[0]
    assert incoming.source == DocumentSource.TELEGRAM and incoming.uploaded_by == "Rute (Telegram)"
    assert kwargs == {"context_text": "Telegram caption: boiler warranty", "external_ref": "telegram:5:6"}
    assert "Inbox" in text


def test_main_exits_cleanly_without_token(monkeypatch, capsys):
    monkeypatch.setattr(telegram_bot.settings, "HUB_TELEGRAM_BOT_TOKEN", "")
    assert telegram_bot.main() == 0
    assert "not configured" in capsys.readouterr().out


def test_build_application_registers_handlers():
    app = telegram_bot.build_application("123456:TEST-TOKEN")
    assert sum(len(h) for h in app.handlers.values()) == 3
