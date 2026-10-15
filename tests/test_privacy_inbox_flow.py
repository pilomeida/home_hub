"""Recognition, the 'what is this?' route, remembering, and the hold — through the real
Inbox service with the fake domains used by the other Inbox tests. Synthetic data only;
files go to a temp folder (inbox_documents_dir)."""

import pytest
from sqlmodel import select

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.inbox_item import InboxItem
from app.models.privacy import DocumentFingerprint
from app.privacy import settings as ps
from app.services import inbox_service
from app.services.domain_classifier import DomainSuggestion
from app.services.ingestion import IncomingFile

pytestmark = [pytest.mark.usefixtures("two_domains", "inbox_documents_dir"), pytest.mark.asyncio]

FROM_BILLER = "Originally from: Billing@Exemplo.pt\nEmail subject: Your invoice"


class SpyClassifier:
    def __init__(self):
        self.calls = 0

    async def __call__(self, path, context):
        self.calls += 1
        return DomainSuggestion(Domain.HOUSE, "manual", 0.9, "ai guess")


async def _receive(session, name, spy, ctx=FROM_BILLER, content=None):
    return await inbox_service.receive_document(
        session, IncomingFile(name, content or f"%PDF {name}".encode(), DocumentSource.EMAIL, "pedro@x"),
        context_text=ctx, external_ref=name, classify=spy)


def _rule(session, kind, pattern):
    session.add(DocumentFingerprint(kind=kind, pattern=pattern, domain="house", category="manual")); session.commit()


async def test_known_document_is_prefilled_by_a_rule_and_no_ai_is_called(session):
    _rule(session, "sender", "billing@exemplo.pt")
    spy = SpyClassifier()
    r = await _receive(session, "a.pdf", spy)
    assert spy.calls == 0
    item = r.inbox_item
    assert (item.suggested_domain, item.suggested_category, item.confidence) == ("house", "manual", 1.0)
    assert item.classifier_note.startswith("Recognised from") and inbox_service.is_confident(item)
    assert r.document.status == DocumentStatus.PENDING_REVIEW          # a human still approves it


async def test_unrecognised_document_still_gets_the_ai_suggestion_while_levels_are_not_binding(session):
    spy = SpyClassifier()
    r = await _receive(session, "b.pdf", spy)
    assert spy.calls == 1 and r.inbox_item.suggested_category == "manual"


async def test_unrecognised_document_is_not_read_by_ai_once_levels_are_binding(session):
    ps.set_enforcing(session, True, None)
    spy = SpyClassifier()
    r = await _receive(session, "c.pdf", spy)
    assert spy.calls == 0
    assert r.inbox_item.suggested_domain is None and "nothing has read it yet" in r.inbox_item.classifier_note
    assert not inbox_service.is_confident(r.inbox_item)


async def test_a_recognised_document_needs_no_ai_even_when_levels_are_binding(session):
    ps.set_enforcing(session, True, None)
    _rule(session, "words", "exemplo")
    spy = SpyClassifier()
    r = await _receive(session, "d.pdf", spy)
    assert spy.calls == 0 and inbox_service.is_confident(r.inbox_item)


async def test_approving_with_remember_creates_rules_then_the_next_one_is_recognised(session):
    spy = SpyClassifier()
    r = await _receive(session, "e.pdf", spy)
    doc = await inbox_service.approve(session, r.document.id, domain_value="house", category="manual",
                                      fields={"item_name": "Boiler"}, reviewed_by="p@x",
                                      remember_sender=True, remember_words="Exemplo SA")
    assert doc.status == DocumentStatus.PROCESSED
    assert {(x.kind, x.pattern, x.category) for x in session.exec(select(DocumentFingerprint))} == {
        ("sender", "billing@exemplo.pt", "manual"), ("words", "exemplo sa", "manual")}
    spy2 = SpyClassifier()
    again = await _receive(session, "f.pdf", spy2)
    assert spy2.calls == 0 and inbox_service.is_confident(again.inbox_item)


async def test_without_remember_no_rules_are_created(session):
    r = await _receive(session, "g.pdf", SpyClassifier())
    await inbox_service.approve(session, r.document.id, domain_value="house", category="manual",
                                fields={"item_name": "Boiler"}, reviewed_by="p@x")
    assert session.exec(select(DocumentFingerprint)).all() == []


async def test_a_failed_filing_creates_no_rules(session):
    r = await _receive(session, "h.pdf", SpyClassifier())
    from app.domains.fields import InvalidClassification
    with pytest.raises(InvalidClassification):
        await inbox_service.approve(session, r.document.id, domain_value="house", category="manual", fields={},
                                    reviewed_by="p@x", remember_sender=True, remember_words="x")
    assert session.exec(select(DocumentFingerprint)).all() == []


async def test_approving_into_a_held_type_marks_nothing_and_reads_nothing(session, two_domains):
    r = await _receive(session, "i.pdf", SpyClassifier())
    ps.set_enforcing(session, True, None)
    with pytest.raises(ps.TierHeld):
        await inbox_service.approve(session, r.document.id, domain_value="house", category="manual",
                                    fields={"item_name": "Boiler"}, reviewed_by="p@x")
    assert session.exec(select(InboxItem)).one().reviewed_at is None
    assert session.get(Document, r.document.id).status == DocumentStatus.PENDING_REVIEW
    assert two_domains["house"].handler.processed == []                # the handler never ran


async def test_once_the_type_has_a_level_the_same_document_files_normally(session, two_domains):
    r = await _receive(session, "j.pdf", SpyClassifier())
    ps.set_enforcing(session, True, None)
    ps.save_type(session, "house", "manual", "house_low", False, None)
    doc = await inbox_service.approve(session, r.document.id, domain_value="house", category="manual",
                                      fields={"item_name": "Boiler"}, reviewed_by="p@x")
    assert doc.status == DocumentStatus.PROCESSED and doc.sensitivity_tier == "house_low"   # stamped at filing


async def test_filing_stamps_the_level_even_while_levels_are_not_binding(session):
    ps.save_type(session, "house", "manual", "house_mid", False, None)
    r = await _receive(session, "k.pdf", SpyClassifier())
    doc = await inbox_service.approve(session, r.document.id, domain_value="house", category="manual",
                                      fields={"item_name": "Boiler"}, reviewed_by="p@x")
    assert doc.sensitivity_tier == "house_mid" and ps.enforcing(session) is False


async def test_a_type_with_no_level_is_stamped_with_nothing(session):
    r = await _receive(session, "l.pdf", SpyClassifier())
    doc = await inbox_service.approve(session, r.document.id, domain_value="house", category="manual",
                                      fields={"item_name": "Boiler"}, reviewed_by="p@x")
    assert doc.sensitivity_tier is None
