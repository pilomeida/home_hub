"""PLAN step 4b: a credit card statement goes through the same intake as every paper. Done when (step 4): Rute's card
statement can be sent by Telegram, approved in the Inbox, read to the cent, and only its new lines stored."""

import asyncio

import pytest
from sqlmodel import select

from app.categories.fields import load_fields
from app.models.account import Account, AccountType
from app.models.document import DocumentSource, DocumentStatus
from app.models.transaction import Transaction
from app.services import card_figures, inbox_service
from app.services.category_classifier import CategorySuggestion
from app.models.category import Category
from app.services.ingestion import IncomingFile
from app.services.taxonomy import ensure_taxonomy
from tests.test_card_figures import INTEREST, STATEMENT


@pytest.fixture()
def rute_card(session, monkeypatch, inbox_documents_dir):
    ensure_taxonomy(session)
    acct = Account(name="Cartão Rute", institution="Santander", account_type=AccountType.CREDIT_CARD
                   if hasattr(AccountType, "CREDIT_CARD") else AccountType.CHECKING)
    session.add(acct); session.commit()
    texts = {}
    from pathlib import Path        # stored files get unique names: the fake PDF's own bytes say which text it holds
    monkeypatch.setattr(card_figures, "read_card_statement",
                        lambda path: card_figures.parse_card_statement(texts.get(Path(path).read_bytes(), STATEMENT)))
    return acct, texts


async def _by_telegram(session, name, content):
    async def classify(file_path, context_text):
        return CategorySuggestion(Category.FINANCIALS, "card_statement", 0.95, "a card statement")
    return await inbox_service.receive_document(
        session, IncomingFile(name, content, DocumentSource.TELEGRAM, "Rute"),
        context_text="Telegram caption: extrato cartão", external_ref=f"telegram:1:{name}", classify=classify)


def test_rutes_card_statement_sent_by_telegram_is_approved_read_and_stored_once(session, rute_card):
    acct, _ = rute_card
    receipt = asyncio.run(_by_telegram(session, "extrato-3.pdf", b"%PDF-1.4 extrato 3"))
    assert inbox_service.is_confident(receipt.inbox_item)
    doc = asyncio.run(inbox_service.approve(session, receipt.document.id, category_value="financials",
                                            paper_kind="card_statement", fields={"account_id": str(acct.id)},
                                            reviewed_by="rute@example.com"))
    assert doc.status == DocumentStatus.PROCESSED and doc.account_id == acct.id
    rows = session.exec(select(Transaction).where(Transaction.account_id == acct.id)).all()
    assert len(rows) == 4 and {t.document_id for t in rows} == {doc.id}
    assert load_fields(doc)["closing_balance"] == "89.57"
    again = asyncio.run(_by_telegram(session, "extrato-3-copy.pdf", b"%PDF-1.4 extrato 3"))
    assert again.duplicate                                          # the same file is caught at the door


def test_a_second_copy_of_the_same_lines_stores_nothing_new(session, rute_card):
    acct, _ = rute_card
    for name, content in (("a.pdf", b"%PDF-1.4 a"), ("b.pdf", b"%PDF-1.4 b")):
        r = asyncio.run(_by_telegram(session, name, content))
        asyncio.run(inbox_service.approve(session, r.document.id, category_value="financials",
                                          paper_kind="card_statement", fields={"account_id": str(acct.id)},
                                          reviewed_by="p@x"))
    assert len(session.exec(select(Transaction).where(Transaction.account_id == acct.id)).all()) == 4


def test_a_statement_that_does_not_add_up_or_has_no_card_stores_nothing_and_says_why(session, rute_card):
    acct, texts = rute_card
    r = asyncio.run(_by_telegram(session, "nocard.pdf", b"%PDF-1.4 n"))
    doc = asyncio.run(inbox_service.approve(session, r.document.id, category_value="financials",
                                            paper_kind="card_statement", fields={}, reviewed_by="p@x"))
    assert doc.status == DocumentStatus.NEEDS_ATTENTION and "which card" in doc.failure_reason
    texts[b"%PDF-1.4 bad"] = INTEREST
    r = asyncio.run(_by_telegram(session, "bad.pdf", b"%PDF-1.4 bad"))
    doc = asyncio.run(inbox_service.approve(session, r.document.id, category_value="financials",
                                            paper_kind="card_statement", fields={"account_id": str(acct.id)},
                                            reviewed_by="p@x"))
    assert doc.status == DocumentStatus.NEEDS_ATTENTION and "add up" in doc.failure_reason
    assert session.exec(select(Transaction).where(Transaction.account_id == acct.id)).all() == []


def test_a_wrapped_bank_download_is_stored_clean_on_every_channel(session, inbox_documents_dir):
    from pathlib import Path
    from app.services.ingestion import receive_file
    clean = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF"
    wrapped = b"\xac\xed\x00\x05ur\x00\x02[B" + b"\x00" * 9 + clean      # a Java byte[] header, as the bank serves it
    first = receive_file(session, IncomingFile("extrato.pdf", wrapped, DocumentSource.TELEGRAM))
    assert Path(first.document.file_path).read_bytes() == clean
    assert receive_file(session, IncomingFile("extrato-clean.pdf", clean, DocumentSource.EMAIL)).duplicate
