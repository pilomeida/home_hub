"""Intake (decision D20): a paper no rule recognises is read ONCE, privately, to decide what it is;
and a Telegram reply never names a private type."""

import pytest
from sqlmodel import select

from app.channels.telegram_bot import reply_for
from app.models.document import DocumentSource
from app.llm_gateway import GatewayError
from app.models.domain import Domain
from app.models.schedule import ProblemEntry
from app.privacy.ratchet import current_ratchet
from app.privacy.tiers import Tier
from app.services.domain_classifier import DomainSuggestion
from app.services.ingestion import IncomingFile
from app.services.inbox_service import receive_document
from tests import privacy_world as pw


def _incoming(name="letter.pdf", content=b"%PDF-1.4 unknown"):
    return IncomingFile(filename=name, content=content, source=DocumentSource.EMAIL, uploaded_by="test")


@pytest.fixture(autouse=True)
def docs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "documents")


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", [True, False])
async def test_an_unrecognised_paper_is_classified_inside_a_high_run_whatever_the_switch(session, binding):
    pw.set_levels(session, on=binding)
    seen = []

    async def classify(path, context_text):
        seen.append(current_ratchet().tier if current_ratchet() else None)
        return DomainSuggestion(Domain.HEALTH, "lab_result", 0.93, "looks like a lab result")

    receipt = await receive_document(session, _incoming(), context_text=None, external_ref="x", classify=classify)
    assert seen == [Tier.HIGH]
    assert receipt.inbox_item.suggested_domain == "health" and receipt.inbox_item.suggested_category == "lab_result"


@pytest.mark.asyncio
async def test_a_failed_private_read_leaves_a_three_part_note_and_the_document_in_the_inbox(session):
    pw.set_levels(session, on=True)
    failure = GatewayError(503, "house_high cannot be served: the Privatemode route is down")   # the production class

    async def classify(path, context_text):
        raise failure

    receipt = await receive_document(session, _incoming(), context_text=None, external_ref="y", classify=classify)
    note = receipt.inbox_item.classifier_note
    assert receipt.inbox_item.suggested_domain is None
    assert note.startswith("Automatic sorting didn't work.") and "Tell me what it is" in note
    assert "confidential AI service" in note                                          # what classify_reason says for it
    assert "house_high" not in note                                                   # no bare technical text shown


@pytest.mark.asyncio
async def test_a_failed_private_read_is_also_on_the_problems_page(session):
    pw.set_levels(session, on=True)
    failure = GatewayError(503, "house_high cannot be served: the Privatemode route is down")

    async def classify(path, context_text):
        raise failure

    receipt = await receive_document(session, _incoming(), context_text=None, external_ref="y", classify=classify)
    problem = session.exec(select(ProblemEntry)).one()
    assert problem.area == "Reading documents" and problem.ref == f"document:{receipt.document.id}"
    assert problem.what == "Automatic sorting didn't work." and problem.why and problem.next_step
    assert problem.technical == str(failure) and "gateway returned 503" in problem.technical
    assert problem.resolved_at is None


@pytest.mark.asyncio
async def test_the_telegram_reply_for_a_private_type_does_not_name_it(session):
    pw.set_levels(session, on=False)

    async def classify(path, context_text):
        return DomainSuggestion(Domain.HEALTH, "lab_result", 0.95, "x")

    receipt = await receive_document(session, _incoming(), context_text=None, external_ref="z", classify=classify)
    reply = reply_for(receipt, "https://hub.example", session)
    assert "Lab result" not in reply and "Health" not in reply
    assert "private document" in reply and "https://hub.example/inbox" in reply


@pytest.mark.asyncio
async def test_the_telegram_reply_for_an_ordinary_type_still_names_it(session):
    pw.set_levels(session, on=False)

    async def classify(path, context_text):
        return DomainSuggestion(Domain.HOUSE, "house_appliance", 0.95, "x")

    receipt = await receive_document(session, _incoming("a.pdf", b"%PDF other"), context_text=None, external_ref="w", classify=classify)
    assert "Appliance" in reply_for(receipt, "https://hub.example", session) or \
           "House" in reply_for(receipt, "https://hub.example", session)
