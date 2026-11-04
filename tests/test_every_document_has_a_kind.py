"""Every filed paper has a kind (work-list B6): the filing guard for new papers, and the one-off fix for the five
Money documents of 2026-08-18 that had none. Documents are built like the live ones (file names, statuses, the
Revolut history on the wrong account); filing runs through the real ingestion core."""

import importlib.util
from pathlib import Path

import pytest
from sqlmodel import select

from app.models.account import Account
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.category import Category
from app.models.schedule import ProblemEntry
from app.models.transaction import Transaction
from app.problems import explain
from app.services import ingestion
from tests.domain_fakes import RecordingHandler, make_fake_spec

_spec = importlib.util.spec_from_file_location("give_kind", Path("scripts/give_every_document_a_kind.py"))
give_kind = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(give_kind)


def _doc(session, filename, status, domain=Category.FINANCIALS, category=None, account_id=None, source=DocumentSource.MANUAL):
    d = Document(filename=filename, file_path=f"/tmp/{filename}", content_hash=f"h-{filename}", source=source,
                 status=status, category=domain, paper_kind=category, account_id=account_id)
    session.add(d); session.commit(); session.refresh(d)
    return d


@pytest.fixture()
def live_like(session):
    santander = Account(name="Santander Current Account", institution="Santander Totta")
    revolut = Account(name="Revolut Current Account", institution="Revolut Bank UAB")
    session.add(santander); session.add(revolut); session.commit()
    docs = {
        "march": _doc(session, "EXTCON202403000356157688020.pdf", DocumentStatus.NEEDS_ATTENTION, account_id=santander.id),
        "revolut": _doc(session, "account-statement_2022-08-01_2026-08-18_en-us_e5ee4c.pdf", DocumentStatus.NEEDS_ATTENTION,
                        account_id=santander.id),
        "metlife": _doc(session, "metlife ago2026.pdf", DocumentStatus.PROCESSED, account_id=santander.id),
        "inbox": _doc(session, "scan.jpg", DocumentStatus.PENDING_REVIEW, domain=None, source=DocumentSource.TELEGRAM),
        "logo": _doc(session, "image001.png", DocumentStatus.DISCARDED, domain=None, source=DocumentSource.EMAIL),
        "unclear": _doc(session, "something.pdf", DocumentStatus.NEEDS_ATTENTION),
        "dental": _doc(session, "FR_3_B_0000059724.pdf", DocumentStatus.PROCESSED, domain=Category.HEALTH, category="medical_bill"),
    }
    session.add(Transaction(document_id=docs["metlife"].id, description="MetLife", amount=109.64,
                            currency="EUR"))
    session.commit()
    return docs, santander, revolut


def test_the_dry_run_says_what_it_would_do_and_changes_nothing(session, live_like):
    docs, _, revolut = live_like
    report = give_kind.run(session, apply=False)
    text = "\n".join(report)
    assert "EXTCON202403000356157688020.pdf (needs_attention): kind statement" in text
    assert f"kind statement and move it to account {revolut.id}" in text
    assert "metlife ago2026.pdf (processed): kind bill" in text
    assert "something.pdf: no rule fits; choose its kind on its page" in text
    assert "'medical_bill' no longer exists" in text and "file_dental_receipt.py" in text
    assert "scan.jpg" not in text and "image001.png" not in text              # waiting for a person / discarded
    session.expire_all()
    assert session.get(Document, docs["march"].id).paper_kind is None


def test_applied_every_filed_document_has_a_kind_and_the_revolut_file_is_on_revolut(session, live_like):
    docs, santander, revolut = live_like
    give_kind.run(session, apply=True)
    session.expire_all()
    assert session.get(Document, docs["march"].id).paper_kind == "statement"
    assert session.get(Document, docs["march"].id).status == DocumentStatus.NEEDS_ATTENTION   # nothing read again
    assert session.get(Document, docs["revolut"].id).account_id == revolut.id
    assert session.get(Document, docs["metlife"].id).paper_kind == "bill"
    assert session.get(Document, docs["inbox"].id).paper_kind is None
    assert len(session.exec(select(Transaction)).all()) == 1                                # no line changes


@pytest.mark.asyncio
async def test_a_paper_the_hub_cannot_give_a_kind_to_goes_to_a_person(session, monkeypatch):
    """The filing guard: a handler that finishes without a kind leaves the paper needing attention, in plain words."""
    from app.categories import registry

    class ForgetsTheKind(RecordingHandler):
        async def process(self, session, document):
            document.paper_kind = None
            return await super().process(session, document)

    spec = make_fake_spec(handler=ForgetsTheKind())
    monkeypatch.setattr(registry, "_specs_cache", {spec.category: spec})
    incoming = ingestion.IncomingFile(filename="doc.pdf", content=b"pdf-bytes", source=DocumentSource.MANUAL,
                                      uploaded_by="pedro@example.com")
    result = await ingestion.ingest(session, incoming, ingestion.Classification(spec.category, "manual", {"item_name": "Boiler"}))
    document = result.document
    assert document.status == DocumentStatus.NEEDS_ATTENTION and document.failure_reason == ingestion.NO_KIND_REASON
    problem = session.exec(select(ProblemEntry)).one()
    assert problem.what == "doc.pdf wasn't processed."
    assert "couldn't work out what kind of paper it is" in problem.why and '"change"' in problem.next_step


def test_the_privacy_refusal_reads_in_plain_words():
    problem = explain("Not done.", "an AI call was made outside any privacy scope, so its privacy level is unknown")
    assert "never sends anything without one" in problem.why and problem.next_step.startswith("Nothing was sent")

