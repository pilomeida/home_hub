from datetime import date

import pytest

from app.categories import registry
from app.categories.financials import handler as financials_handler
from app.categories.financials.kinds import FinancialsKind
from app.categories.financials.overview import financials_overview_card
from app.models.account import Account, AccountType
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.category import Category
from app.models.transaction import Transaction, TransactionType
from app.services.extraction import ExtractedStatement
from app.services.ingestion import Classification, IncomingFile, ingest


def test_financials_is_registered():
    spec = registry.get_spec(Category.FINANCIALS)
    assert spec.label == "Financials"
    assert [c.value for c in spec.kinds] == [c.value for c in FinancialsKind]
    explicit_only = {FinancialsKind.POSITIONS.value, FinancialsKind.LOAN_HISTORY.value}
    assert {c.value for c in spec.kinds if not c.classifiable} == explicit_only
    assert all(c.classifiable for c in spec.kinds if c.value not in explicit_only)
    assert spec.infers_kind is True
    assert [link.url for link in spec.nav_links] == [
        "/financials/bills", "/financials/transactions", "/financials/coming-up", "/financials/bank/",
        "/financials/loans",
        "/financials/utilities/electricity",
    ]


def test_account_field_offers_accounts(session):
    session.add(Account(name="Revolut", institution="Revolut", account_type=AccountType.WALLET))
    session.commit()
    account_field = next(f for f in registry.get_spec(Category.FINANCIALS).fields if f.key == "account_id")
    assert [label for _, label in account_field.options(session)] == ["Revolut"]


def test_overview_card_reports_spend_and_attention(session):
    document = Document(filename="s.pdf", file_path="/tmp/s.pdf", content_hash="c1",
                        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED, category=Category.FINANCIALS)
    broken = Document(filename="bad.pdf", file_path="/tmp/bad.pdf", content_hash="c2",
                      source=DocumentSource.MANUAL, status=DocumentStatus.NEEDS_ATTENTION, category=Category.FINANCIALS)
    session.add(document)
    session.add(broken)
    session.commit()
    session.refresh(document)
    session.add(Transaction(document_id=document.id, description="CONTINENTE", transaction_type=TransactionType.DEBIT, amount=40.0, paid_date=date(2026, 9, 3)))
    session.add(Transaction(document_id=document.id, description="OLD", transaction_type=TransactionType.DEBIT, amount=99.0, paid_date=date(2026, 8, 3)))
    session.commit()

    card = financials_overview_card(session, date(2026, 9, 24))

    assert card.label == "Financials" and card.url == "/financials/bills"
    assert card.lines[0].text == "Spent this month: €40.00"
    assert card.lines[1].attention is True and "1 document" in card.lines[1].text


@pytest.mark.asyncio
async def test_handler_infers_category_and_moves_account_to_its_column(session, monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path)
    account = Account(name="Santander", institution="Santander Totta", account_type=AccountType.CHECKING)
    session.add(account)
    session.commit()
    session.refresh(account)

    async def fake_classify(file_path, client=None):
        return "statement"

    async def fake_extract(file_path, client=None):
        return ExtractedStatement(statement_period="2026-08", transactions=[])

    monkeypatch.setattr(financials_handler, "classify_document", fake_classify)
    monkeypatch.setattr(financials_handler, "extract_statement_transactions", fake_extract)

    result = await ingest(
        session,
        IncomingFile("statement.pdf", b"bytes", DocumentSource.MANUAL),
        Classification(Category.FINANCIALS, None, {"account_id": str(account.id)}),
    )

    document = result.document
    assert document.status == DocumentStatus.PROCESSED
    assert document.paper_kind == "statement"
    assert document.account_id == account.id
    assert document.fields_json == "{}"


@pytest.mark.asyncio
async def test_handler_skips_llm_classification_when_category_is_given(session, monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path)

    async def must_not_classify(file_path, client=None):
        raise AssertionError("classify_document must not run when a category was supplied")

    async def fake_extract(file_path, client=None):
        return ExtractedStatement(statement_period="2026-08", transactions=[])

    monkeypatch.setattr(financials_handler, "classify_document", must_not_classify)
    monkeypatch.setattr(financials_handler, "extract_statement_transactions", fake_extract)

    result = await ingest(
        session, IncomingFile("s.pdf", b"b2", DocumentSource.MANUAL), Classification(Category.FINANCIALS, "statement"),
    )
    assert result.document.status == DocumentStatus.PROCESSED