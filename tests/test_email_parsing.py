from email.message import EmailMessage

from app.channels.email_parsing import MIN_IMAGE_BYTES, parse_email


def _mail(sender="Rute <Rute@Example.com>", subject="Boiler warranty", body="See attached"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "hub@example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = "<abc@mail>"
    msg.set_content(body)
    return msg


def test_extracts_pdf_attachment_and_context():
    msg = _mail()
    msg.add_attachment(b"%PDF-1.4 data", maintype="application", subtype="pdf", filename="warranty.pdf")
    parsed = parse_email(msg.as_bytes())
    assert parsed.sender == "rute@example.com" and parsed.subject == "Boiler warranty"
    assert parsed.message_id == "<abc@mail>"
    assert "Boiler warranty" in parsed.context_text and "See attached" in parsed.context_text
    assert [(a.filename, a.content) for a in parsed.attachments] == [("warranty.pdf", b"%PDF-1.4 data")]


def test_skips_tiny_images_and_unsupported_types():
    msg = _mail()
    msg.add_attachment(b"x" * 100, maintype="image", subtype="png", filename="logo.png")
    msg.add_attachment(b"MZ...", maintype="application", subtype="octet-stream", filename="setup.exe")
    msg.add_attachment(b"y" * (MIN_IMAGE_BYTES + 1), maintype="image", subtype="jpeg", filename="photo.JPG")
    assert [a.filename for a in parse_email(msg.as_bytes()).attachments] == ["photo.JPG"]


def test_finds_attachments_inside_forwarded_message():
    inner = _mail(sender="shop@example.com", subject="Your invoice")
    inner.add_attachment(b"%PDF inner", maintype="application", subtype="pdf", filename="invoice.pdf")
    outer = _mail(subject="Fwd: Your invoice")
    outer.add_attachment(inner)
    parsed = parse_email(outer.as_bytes())
    assert [a.filename for a in parsed.attachments] == ["invoice.pdf"]
    assert parsed.sender == "rute@example.com"


def test_no_attachments():
    assert parse_email(_mail().as_bytes()).attachments == []


def test_gmail_auto_forward_keeps_original_sender_and_records_forwarder():
    # A Gmail forwarding rule keeps the bill's own From and stamps
    # "X-Forwarded-For: <forwarding account> <destination>".
    msg = _mail(sender="Faturas <Faturas@Coopernico.org>", subject="Fatura dezembro")
    msg["X-Forwarded-For"] = "Rute@Example.com hub@example.com"
    parsed = parse_email(msg.as_bytes())
    assert parsed.sender == "faturas@coopernico.org"
    assert parsed.forwarded_by == "rute@example.com"
    assert "Originally from: faturas@coopernico.org" in parsed.context_text


def test_not_forwarded_has_no_forwarder():
    assert parse_email(_mail().as_bytes()).forwarded_by == ""
