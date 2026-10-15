"""Recognition without AI, the hold, and the privacy scope (decisions D12, D13). Synthetic data only."""

import io

import pytest
from sqlmodel import select

from app.llm_gateway import GatewayClient
from app.models.domain import Domain
from app.models.privacy import DocumentFingerprint
from app.privacy import recognise as rec
from app.privacy import settings as ps
from app.privacy.scope import current_scope, privacy_scope


def _pdf_with_text(text: str) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    w = PdfWriter()
    page = w.add_blank_page(width=300, height=200)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = w._add_object(stream)
    buf = io.BytesIO(); w.write(buf)
    return buf.getvalue()


def _rule(session, kind, pattern, domain="financials", category="bill"):
    session.add(DocumentFingerprint(kind=kind, pattern=pattern, domain=domain, category=category)); session.commit()


def test_pdf_text_layer_is_read_and_non_text_files_give_nothing():
    assert "Electricity Exemplo" in rec.pdf_text(_pdf_with_text("Electricity Exemplo invoice"), "a.pdf")
    assert rec.pdf_text(b"\xff\xd8not a pdf", "photo.jpg") == ""
    assert rec.pdf_text(b"garbage", "broken.pdf") == ""            # never raises


def test_original_sender_comes_from_the_forwarded_mail_header_only():
    assert rec.original_sender("Originally from: Billing <Billing@Exemplo.pt>\nEmail subject: x") == "billing@exemplo.pt"
    assert rec.original_sender("Email subject: x") == ""


def test_recognises_by_sender_words_in_pdf_and_filename(session):
    _rule(session, "sender", "billing@exemplo.pt")
    _rule(session, "words", "contrato 4455", "insurance", "home_insurance")
    _rule(session, "filename", "extrato", "financials", "statement")
    a = rec.recognise(session, filename="x.pdf", content=b"", context_text="Originally from: Billing@Exemplo.pt\nEmail subject: fatura")
    b = rec.recognise(session, filename="y.pdf", content=_pdf_with_text("Apolice Contrato 4455 casa"), context_text=None)
    c = rec.recognise(session, filename="Extrato_marco.pdf", content=b"", context_text=None)
    assert (a.domain, a.category) == ("financials", "bill") and "billing@exemplo.pt" in a.reasons[0]
    assert (b.domain, b.category) == ("insurance", "home_insurance")
    assert (c.domain, c.category) == ("financials", "statement")
    assert [r.hits for r in session.exec(select(DocumentFingerprint).order_by(DocumentFingerprint.id))] == [1, 1, 1]


def test_disagreeing_or_no_rules_means_not_recognised(session):
    _rule(session, "words", "exemplo", "financials", "bill")
    _rule(session, "filename", "apolice", "insurance", "home_insurance")
    assert rec.recognise(session, filename="apolice.pdf", content=_pdf_with_text("Exemplo"), context_text=None) is None   # conflict
    assert rec.recognise(session, filename="unknown.pdf", content=b"", context_text="nothing relevant") is None
    assert all(r.hits == 0 for r in session.exec(select(DocumentFingerprint)))       # a non-match counts nothing


def test_a_rule_for_a_type_that_no_longer_exists_is_ignored(session):
    _rule(session, "words", "exemplo", "financials", "no_such_type")
    assert rec.recognise(session, filename="a.pdf", content=b"", context_text="exemplo") is None


def test_photos_are_recognised_only_by_caption_or_sender(session):
    _rule(session, "words", "electricity", "financials", "bill")
    assert rec.recognise(session, filename="photo.jpg", content=b"\xff\xd8", context_text="Telegram caption: electricity bill") is not None
    assert rec.recognise(session, filename="photo.jpg", content=b"\xff\xd8", context_text=None) is None


def test_remember_creates_each_rule_once(session):
    made = rec.remember(session, domain="financials", category="bill", by="p@x", sender="Billing@Exemplo.pt", words=" Exemplo SA ")
    again = rec.remember(session, domain="financials", category="bill", by="p@x", sender="billing@exemplo.pt", words="exemplo sa")
    assert len(made) == 2 and again == []
    assert {(r.kind, r.pattern) for r in session.exec(select(DocumentFingerprint))} == {("sender", "billing@exemplo.pt"), ("words", "exemplo sa")}


# --- the scope -----------------------------------------------------------------

def test_scope_is_a_noop_while_levels_are_not_binding(session):
    with privacy_scope(session, Domain.FINANCIALS, "statement") as s:
        assert s is None and current_scope() is None


def test_scope_carries_the_type_level_and_ends_cleanly(session):
    ps.set_enforcing(session, True, None)
    ps.save_type(session, "financials", "statement", "house_mid", False, None)
    with privacy_scope(session, Domain.FINANCIALS, "statement"):
        assert current_scope().tier.value == "house_mid" and current_scope().redaction is None
    assert current_scope() is None


def test_scope_holds_an_unset_type(session):
    ps.set_enforcing(session, True, None)
    with pytest.raises(ps.TierHeld):
        with privacy_scope(session, Domain.FINANCIALS, "statement"):
            pass


def test_unknown_category_uses_the_strictest_level_of_the_domain_and_holds_if_any_unset(session):
    ps.set_enforcing(session, True, None)
    for cat, tier in (("bill", "house_low"), ("statement", "house_mid"), ("statement_positions", "house_low")):
        ps.save_type(session, "financials", cat, tier, False, None)
    with pytest.raises(ps.TierHeld):                                   # loan_history still unset
        with privacy_scope(session, Domain.FINANCIALS, None):
            pass
    ps.save_type(session, "financials", "loan_history", "house_low", False, None)
    with privacy_scope(session, Domain.FINANCIALS, None):
        assert current_scope().tier.value == "house_mid"


def test_redaction_switch_puts_a_redactor_in_the_scope(session):
    ps.set_enforcing(session, True, None)
    ps.save_type(session, "financials", "bill", "house_mid", True, None)
    with privacy_scope(session, Domain.FINANCIALS, "bill"):
        assert current_scope().redaction is not None


@pytest.mark.asyncio
async def test_gateway_calls_inside_a_scope_carry_the_level_and_an_explicit_value_wins(session):
    import httpx, json
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"text": "ok", "stop_reason": "end_turn", "usage": {}, "catalog_key": "k"})

    client = GatewayClient("http://gw.test", "t", transport=httpx.MockTransport(handler))
    ps.set_enforcing(session, True, None)
    ps.save_type(session, "financials", "bill", "house_mid", False, None)
    await client.run("classification", "s", "u")                       # outside any scope: unchanged
    with privacy_scope(session, Domain.FINANCIALS, "bill"):
        await client.run("classification", "s", "u")
        await client.run("classification", "s", "u", sensitivity="house_low")
    assert [b.get("sensitivity") for b in bodies] == [None, "house_mid", "house_low"]


@pytest.mark.asyncio
async def test_scope_does_not_leak_into_a_concurrent_task(session):
    import asyncio
    ps.set_enforcing(session, True, None)
    ps.save_type(session, "financials", "bill", "house_mid", False, None)
    seen = {}

    async def other():
        await asyncio.sleep(0)
        seen["other"] = current_scope()

    with privacy_scope(session, Domain.FINANCIALS, "bill"):
        task = asyncio.create_task(other())     # copies the context at creation: sees the scope, by design
        await task
    assert seen["other"] is not None            # tasks started INSIDE the scope belong to the document
    assert current_scope() is None              # and nothing remains after it ends
