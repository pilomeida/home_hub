import json

import pytest

from app.models.transaction import Category, Nature
from app.services.classification_engine import (
    ClassificationResult,
    MerchantResolutionError,
    ResolvedMerchant,
    classify_transaction,
    normalize_provider,
    resolve_merchant_via_llm,
)


def test_normalize_provider_strips_trailing_store_code():
    assert normalize_provider("MODELO HIPER 2640-MAFR") == "modelo hiper"


def test_normalize_provider_strips_trailing_location_word():
    assert normalize_provider("MODELO HIPER MAFRA") == "modelo hiper"


def test_normalize_provider_leaves_bare_name_unchanged():
    assert normalize_provider("MODELO HIPER") == "modelo hiper"


def test_normalize_provider_strips_repeated_trailing_location_words():
    assert normalize_provider("ALDI MAFRA MAFRA") == "aldi"


def test_normalize_provider_strips_single_trailing_location_word():
    assert normalize_provider("INTERMARCHE MAFRA") == "intermarche"


def test_normalize_provider_does_not_strip_fused_substring():
    # "ACMAFRA" ends in "mafra" as a substring of one fused word, not a
    # separate trailing location token preceded by whitespace — must not
    # be stripped, since that would corrupt an unrelated merchant name.
    assert normalize_provider("AUTOMAFRA - PNEUS ACMAFRA") == "automafra - pneus acmafra"


def test_normalize_provider_collapses_whitespace():
    assert normalize_provider("  MODELO   HIPER  ") == "modelo hiper"


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text

    async def create(self, **kwargs):
        return _FakeMessage(self._response_text)


class _FakeAnthropicClient:
    def __init__(self, response_text):
        self.messages = _FakeMessages(response_text)


@pytest.mark.asyncio
async def test_resolve_merchant_via_llm_parses_valid_response():
    response = json.dumps({
        "canonical_name": "Modelo Hiper",
        "category": "groceries",
        "nature": "essential",
    })
    client = _FakeAnthropicClient(response)

    result = await resolve_merchant_via_llm("MODELO HIPER 2640-MAFR", client=client)

    assert isinstance(result, ResolvedMerchant)
    assert result.canonical_name == "Modelo Hiper"
    assert result.category == Category.GROCERIES
    assert result.nature == Nature.ESSENTIAL


@pytest.mark.asyncio
async def test_resolve_merchant_via_llm_raises_on_malformed_json():
    client = _FakeAnthropicClient("not json")

    with pytest.raises(MerchantResolutionError):
        await resolve_merchant_via_llm("SOME PROVIDER", client=client)


@pytest.mark.asyncio
async def test_resolve_merchant_via_llm_raises_on_invalid_category():
    response = json.dumps({
        "canonical_name": "Some Shop", "category": "not_a_real_category", "nature": "essential",
    })
    client = _FakeAnthropicClient(response)

    with pytest.raises(MerchantResolutionError):
        await resolve_merchant_via_llm("SOME SHOP", client=client)


@pytest.mark.asyncio
async def test_classify_transaction_reuses_existing_merchant(session):
    from app.models.document import Document, DocumentSource
    from app.models.merchant import Merchant
    from app.models.transaction import Transaction

    merchant = Merchant(
        canonical_name="Modelo Hiper", default_category=Category.GROCERIES,
        default_nature=Nature.ESSENTIAL, normalized_key="modelo hiper",
    )
    session.add(merchant)
    session.commit()
    session.refresh(merchant)

    document = Document(
        filename="s.pdf", file_path="/tmp/s.pdf", content_hash="hash-classify-1",
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="MODELO HIPER MAFRA",
        category=Category.GROCERIES, amount=42.0, currency="EUR",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    result = await classify_transaction(session, transaction, client=_FakeAnthropicClient("{}"))
    session.commit()

    assert isinstance(result, ClassificationResult)
    assert result.created_new_merchant is False
    assert result.merchant.id == merchant.id
    assert transaction.merchant_id == merchant.id
    assert transaction.nature == Nature.ESSENTIAL


@pytest.mark.asyncio
async def test_classify_transaction_creates_new_merchant_via_llm_fallback(session):
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction

    document = Document(
        filename="s2.pdf", file_path="/tmp/s2.pdf", content_hash="hash-classify-2",
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="LOJA NOVA DESCONHECIDA",
        category=Category.OTHER_EXPENSE, amount=15.0, currency="EUR",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    response = json.dumps({
        "canonical_name": "Loja Nova", "category": "shopping", "nature": "discretionary",
    })
    result = await classify_transaction(session, transaction, client=_FakeAnthropicClient(response))
    session.commit()

    assert result.created_new_merchant is True
    assert result.merchant.canonical_name == "Loja Nova"
    assert transaction.merchant_id == result.merchant.id
    assert transaction.nature == Nature.DISCRETIONARY


@pytest.mark.asyncio
async def test_classify_transaction_inherits_account_id_from_document(session):
    from app.models.account import Account, AccountType
    from app.models.document import Document, DocumentSource
    from app.models.merchant import Merchant
    from app.models.transaction import Transaction

    account = Account(name="Current Account", institution="Millennium BCP", currency="EUR", account_type=AccountType.CHECKING)
    session.add(account)
    session.commit()
    session.refresh(account)

    document = Document(
        filename="s3.pdf", file_path="/tmp/s3.pdf", content_hash="hash-classify-3",
        source=DocumentSource.MANUAL, account_id=account.id,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    merchant = Merchant(
        canonical_name="Modelo Hiper", default_category=Category.GROCERIES,
        normalized_key="modelo hiper",
    )
    session.add(merchant)
    session.commit()

    transaction = Transaction(
        document_id=document.id, provider="MODELO HIPER", category=Category.GROCERIES,
        amount=20.0, currency="EUR",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    await classify_transaction(session, transaction, client=_FakeAnthropicClient("{}"))
    session.commit()

    assert transaction.account_id == account.id


@pytest.mark.asyncio
async def test_classify_transaction_does_not_overwrite_existing_account_id(session):
    from app.models.account import Account, AccountType
    from app.models.document import Document, DocumentSource
    from app.models.merchant import Merchant
    from app.models.transaction import Transaction

    account_a = Account(name="Account A", institution="Millennium BCP", currency="EUR", account_type=AccountType.CHECKING)
    account_b = Account(name="Account B", institution="Novo Banco", currency="EUR", account_type=AccountType.CHECKING)
    session.add(account_a)
    session.add(account_b)
    session.commit()
    session.refresh(account_a)
    session.refresh(account_b)

    document = Document(
        filename="s4.pdf", file_path="/tmp/s4.pdf", content_hash="hash-classify-4",
        source=DocumentSource.MANUAL, account_id=account_a.id,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    merchant = Merchant(
        canonical_name="Modelo Hiper", default_category=Category.GROCERIES,
        normalized_key="modelo hiper",
    )
    session.add(merchant)
    session.commit()

    transaction = Transaction(
        document_id=document.id, provider="MODELO HIPER", category=Category.GROCERIES,
        amount=20.0, currency="EUR", account_id=account_b.id,
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    await classify_transaction(session, transaction, client=_FakeAnthropicClient("{}"))
    session.commit()

    assert transaction.account_id == account_b.id


@pytest.mark.asyncio
async def test_classify_transaction_does_not_commit_internally(session):
    from app.models.document import Document, DocumentSource
    from app.models.merchant import Merchant
    from app.models.transaction import Transaction
    from sqlmodel import select

    document = Document(
        filename="s5.pdf", file_path="/tmp/s5.pdf", content_hash="hash-classify-5",
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="LOJA NOVA DESCONHECIDA",
        category=Category.OTHER_EXPENSE, amount=15.0, currency="EUR",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    response = json.dumps({
        "canonical_name": "Loja Nova", "category": "shopping", "nature": "discretionary",
    })
    result = await classify_transaction(session, transaction, client=_FakeAnthropicClient(response))
    assert result.created_new_merchant is True

    session.rollback()

    found = session.exec(
        select(Merchant).where(Merchant.normalized_key == "loja nova desconhecida")
    ).first()
    assert found is None
