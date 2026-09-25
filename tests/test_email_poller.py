from email.message import EmailMessage

import pytest
from sqlmodel import Session

from app.channels import email_poller
from app.channels.email_poller import FAILED_FOLDER, IGNORED_FOLDER, PROCESSED_FOLDER, poll_once
from app.models.document import DocumentSource
from app.services.inbox_service import InboxReceipt

ALLOWED = frozenset({"rute@example.com"})


def _raw(sender="rute@example.com", with_pdf=True):
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = "Docs"
    msg["Message-ID"] = "<m1@x>"
    msg.set_content("hello")
    if with_pdf:
        msg.add_attachment(b"%PDF a", maintype="application", subtype="pdf", filename="a.pdf")
        msg.add_attachment(b"%PDF b", maintype="application", subtype="pdf", filename="b.pdf")
    return msg.as_bytes()


class FakeMailbox:
    def __init__(self, messages):
        self.messages = dict(messages)
        self.moves = []
        self.folders = None

    def ensure_folders(self, names):
        self.folders = list(names)

    def list_uids(self):
        return list(self.messages)

    def fetch(self, uid):
        return self.messages[uid]

    def move(self, uid, folder):
        self.moves.append((uid, folder))

    def close(self):
        pass


@pytest.fixture()
def recorder():
    calls = []

    async def fake_receive(session, incoming, **kwargs):
        calls.append((incoming, kwargs))
        return InboxReceipt(document=None, inbox_item=None, duplicate=False)

    return calls, fake_receive


@pytest.mark.asyncio
async def test_allowed_sender_each_attachment_received_then_processed(engine, recorder):
    calls, fake_receive = recorder
    mailbox = FakeMailbox({b"1": _raw()})

    report = await poll_once(mailbox, lambda: Session(engine), ALLOWED, receive=fake_receive)

    assert [c[0].filename for c in calls] == ["a.pdf", "b.pdf"]
    incoming, kwargs = calls[0]
    assert incoming.source == DocumentSource.EMAIL and incoming.uploaded_by == "rute@example.com"
    assert kwargs["external_ref"] == "email:<m1@x>#0" and "Docs" in kwargs["context_text"]
    assert mailbox.moves == [(b"1", PROCESSED_FOLDER)]
    assert report.processed == 1 and report.documents_created == 2
    assert set(mailbox.folders) == {PROCESSED_FOLDER, IGNORED_FOLDER, FAILED_FOLDER}


@pytest.mark.asyncio
async def test_unknown_sender_is_ignored_without_llm(engine, recorder):
    calls, fake_receive = recorder
    mailbox = FakeMailbox({b"1": _raw(sender="spam@evil.test")})
    report = await poll_once(mailbox, lambda: Session(engine), ALLOWED, receive=fake_receive)
    assert calls == [] and mailbox.moves == [(b"1", IGNORED_FOLDER)] and report.ignored == 1


@pytest.mark.asyncio
async def test_no_attachment_is_ignored(engine, recorder):
    _, fake_receive = recorder
    mailbox = FakeMailbox({b"1": _raw(with_pdf=False)})
    await poll_once(mailbox, lambda: Session(engine), ALLOWED, receive=fake_receive)
    assert mailbox.moves == [(b"1", IGNORED_FOLDER)]


@pytest.mark.asyncio
async def test_failure_moves_to_failed_and_continues(engine):
    async def exploding_receive(session, incoming, **kwargs):
        if incoming.filename == "a.pdf":
            raise RuntimeError("disk full")
        return InboxReceipt(document=None, inbox_item=None, duplicate=False)

    mailbox = FakeMailbox({b"1": _raw(), b"2": _raw(with_pdf=False)})
    report = await poll_once(mailbox, lambda: Session(engine), ALLOWED, receive=exploding_receive)
    assert mailbox.moves == [(b"1", FAILED_FOLDER), (b"2", IGNORED_FOLDER)] and report.failed == 1


@pytest.mark.asyncio
async def test_caps_messages_per_run(engine, recorder, monkeypatch):
    _, fake_receive = recorder
    monkeypatch.setattr(email_poller, "MAX_MESSAGES_PER_RUN", 1)
    mailbox = FakeMailbox({b"1": _raw(), b"2": _raw()})
    await poll_once(mailbox, lambda: Session(engine), ALLOWED, receive=fake_receive)
    assert [m[0] for m in mailbox.moves] == [b"1"]


def test_main_exits_cleanly_when_unconfigured(monkeypatch, capsys):
    monkeypatch.setattr(email_poller.settings, "HUB_IMAP_HOST", "")
    assert email_poller.main() == 0
    assert "not configured" in capsys.readouterr().out
