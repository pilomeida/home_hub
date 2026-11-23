"""PLAN step 11c: every page about one paper shows what the Hub read and a small preview of the paper's first page,
from one shared component; the picture is drawn on the server and the paper itself comes only from its file route.
A paper that can't be shown says what, why and what to do."""

import shutil
from datetime import date

import pytest
from sqlmodel import select

from app.categories.health.extraction import ExtractedHealth
from app.categories.health.store import apply_extraction
from app.categories.fields import file_url
from app.categories.registry import document_url
from app.config import settings
from app.models.category import Category
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.inbox_item import InboxItem
from app.models.record import Record
from app.models.transaction import Transaction, TransactionType
from app.services.storage import save_file
from tests.knowledge_factories import make_document, make_record

HAS_POPPLER = shutil.which("pdftoppm") is not None


def _pdf(text: str = "Fatura EDP 61.40 EUR") -> bytes:
    """A real one-page PDF (Helvetica text), so poppler draws it as it would an uploaded paper."""
    stream = f"BT /F1 18 Tf 60 760 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


@pytest.fixture()
def previews(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "db" / "hub.db")
    return tmp_path / "db" / "previews"


def _paper(session, category, kind, filename="paper.pdf", content=None, fields=None):
    path = save_file(filename, content if content is not None else _pdf())          # where an upload is stored
    return make_document(session, category=category, paper_kind=kind, filename=filename, file_path=path, fields=fields)


def _has_component(html: str, doc: Document) -> bool:
    return ('class="pv"' in html and f'data-pv-file="{file_url(doc)}"' in html
            and f'/documents/{doc.id}/preview.png' in html and "full view or side by side" in html)


@pytest.mark.skipif(not HAS_POPPLER, reason="poppler (pdftoppm) not installed")
def test_the_preview_is_the_first_page_drawn_on_the_server_and_kept(client, session, previews):
    doc = _paper(session, Category.FINANCIALS, "bill")
    first = client.get(f"/documents/{doc.id}/preview.png")
    assert first.status_code == 200 and first.headers["content-type"] == "image/png"
    assert first.content.startswith(b"\x89PNG")
    [kept] = list(previews.iterdir())
    assert kept.read_bytes() == first.content
    assert client.get(f"/documents/{doc.id}/preview.png").content == first.content     # drawn once, then kept


def test_no_picture_for_a_missing_file_a_non_pdf_or_an_unknown_paper(client, session, previews):
    gone = make_document(session, category=Category.HOUSE, paper_kind="house_appliance", filename="gone.pdf",
                         file_path=str(settings.DOCUMENTS_DIR / "gone.pdf"))
    photo = _paper(session, Category.HOUSE, "house_appliance", filename="boiler.png", content=b"\x89PNG\r\n\x1a\nx")
    for doc_id in (gone.id, photo.id, 99999):
        r = client.get(f"/documents/{doc_id}/preview.png")
        assert r.status_code == 404 and r.json()["detail"]["what"] == "No preview."
    assert not previews.exists()


@pytest.mark.parametrize("category,kind,fields", [
    (Category.FINANCIALS, "bill", None), (Category.FINANCIALS, "statement", None),
    (Category.HOUSE, "house_appliance", {"item_name": "Boiler"}), (Category.INSURANCE, "policy", None),
    (Category.IDENTITY, "passport", None), (Category.VEHICLES, "inspection", None),
])
def test_every_paper_page_shows_what_was_read_beside_the_preview(client, session, category, kind, fields):
    doc = _paper(session, category, kind, fields=fields)
    if category == Category.FINANCIALS:
        session.add(Transaction(document_id=doc.id, description="EDP Comercial", amount=61.4, currency="EUR",
                                due_date=date(2026, 12, 5), transaction_type=TransactionType.DEBIT))
        session.commit()
    html = client.get(document_url(doc)).text
    assert _has_component(html, doc), category
    assert 'class="paper-page"' in html and "What the Hub read" in html
    assert html.index('class="paper-read"') < html.index('class="pv"')


def test_a_health_paper_shows_the_preview_not_a_full_frame(client, session):
    doc = _paper(session, Category.HEALTH, "lab_result", filename="lab.pdf")
    apply_extraction(session, doc, ExtractedHealth(
        person_name="Ana Exemplo", document_date=date(2026, 3, 12), provider="Lab", record_kind="test",
        areas=[], conditions=[], summary="Ana: lipid panel", measurements=[]))
    html = client.get(f"/health/documents/{doc.id}").text
    assert _has_component(html, doc) and "<iframe" not in html and "What the Hub read" in html


def test_inbox_cards_show_the_preview(client, session):
    path = save_file("vodafone.pdf", _pdf("Vodafone"))
    doc = Document(filename="vodafone.pdf", file_path=path, content_hash="v1", source=DocumentSource.EMAIL,
                   status=DocumentStatus.PENDING_REVIEW)
    session.add(doc); session.commit(); session.refresh(doc)
    session.add(InboxItem(document_id=doc.id, suggested_category="financials", suggested_paper_kind="bill", confidence=0.95))
    session.commit()
    assert _has_component(client.get("/inbox").text, doc)
    assert _has_component(client.get(f"/inbox/{doc.id}/card").text, doc)


def test_a_house_record_shows_its_attached_paper(client, session):
    attached = _paper(session, Category.HOUSE, "warranty_invoice", filename="receipt.pdf")
    record = make_record(session, category=Category.HOUSE, paper_kind="maintenance_log",
                         fields={"item_name": "Boiler"}, document_id=attached.id)
    assert _has_component(client.get(f"/house/records/{record.id}").text, attached)


def _letters(session):
    from app.services import paper_types
    paper_types.add_area(session, "Letters", "", first_kind="Letter", by=None)        # an added area (step 12a)
    return Category("letters")


def test_an_added_areas_paper_page_shows_the_preview_too(client, session):
    doc = _paper(session, _letters(session), "letter")
    assert _has_component(client.get(f"/areas/letters/documents/{doc.id}").text, doc)


def test_a_paper_that_cannot_be_shown_says_what_why_and_what_to_do(client, session):
    letters = _letters(session)
    gone = make_document(session, category=letters, paper_kind="letter", filename="gone.pdf",
                         file_path=str(settings.DOCUMENTS_DIR / "gone.pdf"))
    html = client.get(f"/areas/letters/documents/{gone.id}").text
    assert "No preview." in html and "is not on the server" in html and "+ Add a paper" in html
    heic = _paper(session, letters, "letter", filename="scan.heic", content=b"heic")
    html = client.get(f"/areas/letters/documents/{heic.id}").text
    assert "No preview." in html and "HEIC" in html and "Open the file" in html and "preview.png" not in html


def test_an_image_previews_through_its_own_file_route(client, session):
    photo = _paper(session, _letters(session), "letter", filename="letter.jpg", content=b"\xff\xd8\xff jpeg")
    html = client.get(f"/areas/letters/documents/{photo.id}").text
    assert f'<img src="{file_url(photo)}"' in html and "preview.png" not in html


def test_the_bank_feeds_statement_has_no_paper_to_show(client, session):
    from app.categories.financials.feed import feed_statement
    from app.models.account import Account
    account = Account(name="Conta", institution="Santander")
    session.add(account); session.commit(); session.refresh(account)
    feed = feed_statement(session, account.id, "Santander")                  # how the bank sync makes it
    session.commit()
    html = client.get(f"/financials/bills/{feed.id}").text
    assert 'class="pv"' not in html and "there is no file" in html
