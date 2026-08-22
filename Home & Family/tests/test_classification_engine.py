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
async def test_classify_transaction_does_not_overwrite_existing_nature(session):
    from app.models.document import Document, DocumentSource
    from app.models.merchant import Merchant
    from app.models.transaction import Transaction

    merchant = Merchant(
        canonical_name="Modelo Hiper", default_category=Category.GROCERIES,
        default_nature=Nature.ESSENTIAL, normalized_key="modelo hiper nature guard",
    )
    session.add(merchant)
    session.commit()
    session.refresh(merchant)

    document = Document(
        filename="s6.pdf", file_path="/tmp/s6.pdf", content_hash="hash-classify-6",
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="MODELO HIPER NATURE GUARD",
        category=Category.GROCERIES, amount=20.0, currency="EUR",
        nature=Nature.DISCRETIONARY,
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    await classify_transaction(session, transaction, client=_FakeAnthropicClient("{}"))
    session.commit()

    assert transaction.nature == Nature.DISCRETIONARY


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


def test_detect_recurring_candidates_flags_three_consecutive_similar_months(session):
    from app.models.document import Document, DocumentSource
    from app.models.merchant import Merchant
    from app.models.transaction import Transaction
    from app.services.classification_engine import detect_recurring_candidates

    merchant = Merchant(
        canonical_name="Netflix", default_category=Category.SUBSCRIPTIONS,
        normalized_key="netflix",
    )
    session.add(merchant)
    session.commit()
    session.refresh(merchant)

    for i, period in enumerate(["2026-05", "2026-06", "2026-07"]):
        document = Document(
            filename=f"netflix-{i}.pdf", file_path=f"/tmp/netflix-{i}.pdf",
            content_hash=f"hash-netflix-{i}", source=DocumentSource.MANUAL,
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        session.add(Transaction(
            document_id=document.id, provider="NETFLIX", category=Category.SUBSCRIPTIONS,
            amount=12.99, currency="EUR", statement_period=period, merchant_id=merchant.id,
        ))
    session.commit()

    candidates = detect_recurring_candidates(session)

    assert merchant.id in [m.id for m in candidates]


def test_detect_recurring_candidates_skips_already_reviewed_merchant(session):
    from app.models.document import Document, DocumentSource
    from app.models.merchant import Merchant
    from app.models.transaction import Transaction
    from app.services.classification_engine import detect_recurring_candidates

    merchant = Merchant(
        canonical_name="Netflix", default_category=Category.SUBSCRIPTIONS,
        normalized_key="netflix-reviewed", recurring_reviewed=True,
    )
    session.add(merchant)
    session.commit()
    session.refresh(merchant)

    for i, period in enumerate(["2026-05", "2026-06", "2026-07"]):
        document = Document(
            filename=f"netflix-r-{i}.pdf", file_path=f"/tmp/netflix-r-{i}.pdf",
            content_hash=f"hash-netflix-r-{i}", source=DocumentSource.MANUAL,
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        session.add(Transaction(
            document_id=document.id, provider="NETFLIX", category=Category.SUBSCRIPTIONS,
            amount=12.99, currency="EUR", statement_period=period, merchant_id=merchant.id,
        ))
    session.commit()

    candidates = detect_recurring_candidates(session)

    assert merchant.id not in [m.id for m in candidates]


def test_detect_recurring_candidates_ignores_two_month_run(session):
    from app.models.document import Document, DocumentSource
    from app.models.merchant import Merchant
    from app.models.transaction import Transaction
    from app.services.classification_engine import detect_recurring_candidates

    merchant = Merchant(
        canonical_name="One-off Shop", default_category=Category.SHOPPING,
        normalized_key="one-off shop",
    )
    session.add(merchant)
    session.commit()
    session.refresh(merchant)

    for i, period in enumerate(["2026-05", "2026-06"]):
        document = Document(
            filename=f"oneoff-{i}.pdf", file_path=f"/tmp/oneoff-{i}.pdf",
            content_hash=f"hash-oneoff-{i}", source=DocumentSource.MANUAL,
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        session.add(Transaction(
            document_id=document.id, provider="ONE-OFF SHOP", category=Category.SHOPPING,
            amount=30.0, currency="EUR", statement_period=period, merchant_id=merchant.id,
        ))
    session.commit()

    candidates = detect_recurring_candidates(session)

    assert merchant.id not in [m.id for m in candidates]


def test_detect_recurring_candidates_ignores_run_with_dissimilar_amounts(session):
    from app.models.document import Document, DocumentSource
    from app.models.merchant import Merchant
    from app.models.transaction import Transaction
    from app.services.classification_engine import detect_recurring_candidates

    merchant = Merchant(
        canonical_name="Groceries Chain", default_category=Category.GROCERIES,
        normalized_key="groceries chain",
    )
    session.add(merchant)
    session.commit()
    session.refresh(merchant)

    amounts = [15.0, 80.0, 12.0]
    for i, (period, amount) in enumerate(zip(["2026-05", "2026-06", "2026-07"], amounts)):
        document = Document(
            filename=f"groc-{i}.pdf", file_path=f"/tmp/groc-{i}.pdf",
            content_hash=f"hash-groc-{i}", source=DocumentSource.MANUAL,
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        session.add(Transaction(
            document_id=document.id, provider="GROCERIES CHAIN", category=Category.GROCERIES,
            amount=amount, currency="EUR", statement_period=period, merchant_id=merchant.id,
        ))
    session.commit()

    candidates = detect_recurring_candidates(session)

    assert merchant.id not in [m.id for m in candidates]


def test_detect_recurring_candidates_flags_four_month_alternating_amounts(session):
    from app.models.document import Document, DocumentSource
    from app.models.merchant import Merchant
    from app.models.transaction import Transaction
    from app.services.classification_engine import detect_recurring_candidates

    merchant = Merchant(
        canonical_name="Alternating Service", default_category=Category.SUBSCRIPTIONS,
        normalized_key="alternating-service",
    )
    session.add(merchant)
    session.commit()
    session.refresh(merchant)

    amounts = [110.0, 90.0, 110.0, 90.0]
    for i, (period, amount) in enumerate(zip(["2026-01", "2026-02", "2026-03", "2026-04"], amounts)):
        document = Document(
            filename=f"alt-{i}.pdf", file_path=f"/tmp/alt-{i}.pdf",
            content_hash=f"hash-alt-{i}", source=DocumentSource.MANUAL,
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        session.add(Transaction(
            document_id=document.id, provider="ALTERNATING SERVICE", category=Category.SUBSCRIPTIONS,
            amount=amount, currency="EUR", statement_period=period, merchant_id=merchant.id,
        ))
    session.commit()

    candidates = detect_recurring_candidates(session)

    assert merchant.id in [m.id for m in candidates]


def test_detect_debt_candidates_flags_person_transfer_above_threshold(session):
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction
    from app.services.classification_engine import detect_debt_candidates

    document = Document(
        filename="transfer.pdf", file_path="/tmp/transfer.pdf",
        content_hash="hash-transfer-1", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id,
        provider="TRF CRED SEPA+ P/ EDUARDO MANUEL DA SILVA-17471463",
        category=Category.OTHER_EXPENSE, amount=5000.0, currency="EUR",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    candidates = detect_debt_candidates(session)

    assert transaction.id in [t.id for t in candidates]


def test_detect_debt_candidates_ignores_small_amount(session):
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction
    from app.services.classification_engine import detect_debt_candidates

    document = Document(
        filename="mbway.pdf", file_path="/tmp/mbway.pdf",
        content_hash="hash-mbway-1", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="TRF MBWAY P/ JOAO SANTOS",
        category=Category.OTHER_EXPENSE, amount=20.0, currency="EUR",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    candidates = detect_debt_candidates(session)

    assert transaction.id not in [t.id for t in candidates]


def test_detect_debt_candidates_ignores_grocery_category(session):
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction
    from app.services.classification_engine import detect_debt_candidates

    document = Document(
        filename="groceries-big.pdf", file_path="/tmp/groceries-big.pdf",
        content_hash="hash-groceries-big-1", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="MODELO HIPER MAFRA",
        category=Category.GROCERIES, amount=600.0, currency="EUR",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    candidates = detect_debt_candidates(session)

    assert transaction.id not in [t.id for t in candidates]


def test_detect_debt_candidates_skips_already_reviewed(session):
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction
    from app.services.classification_engine import detect_debt_candidates

    document = Document(
        filename="transfer2.pdf", file_path="/tmp/transfer2.pdf",
        content_hash="hash-transfer-2", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id,
        provider="TRF CRED SEPA+ P/ MARIA COSTA",
        category=Category.OTHER_EXPENSE, amount=5000.0, currency="EUR",
        debt_candidate_reviewed=True,
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    candidates = detect_debt_candidates(session)

    assert transaction.id not in [t.id for t in candidates]


def test_get_needs_review_queue_aggregates_all_three_categories(session):
    from app.models.document import Document, DocumentSource
    from app.models.merchant import Merchant
    from app.models.transaction import Transaction
    from app.services.classification_engine import get_needs_review_queue

    unconfirmed = Merchant(
        canonical_name="New Shop", default_category=Category.SHOPPING,
        normalized_key="new shop",
    )
    session.add(unconfirmed)
    session.commit()
    session.refresh(unconfirmed)

    recurring_merchant = Merchant(
        canonical_name="Netflix", default_category=Category.SUBSCRIPTIONS,
        normalized_key="netflix-queue", confirmed=True,
    )
    session.add(recurring_merchant)
    session.commit()
    session.refresh(recurring_merchant)
    for i, period in enumerate(["2026-05", "2026-06", "2026-07"]):
        document = Document(
            filename=f"q-netflix-{i}.pdf", file_path=f"/tmp/q-netflix-{i}.pdf",
            content_hash=f"hash-q-netflix-{i}", source=DocumentSource.MANUAL,
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        session.add(Transaction(
            document_id=document.id, provider="NETFLIX", category=Category.SUBSCRIPTIONS,
            amount=12.99, currency="EUR", statement_period=period,
            merchant_id=recurring_merchant.id,
        ))
    session.commit()

    debt_document = Document(
        filename="q-transfer.pdf", file_path="/tmp/q-transfer.pdf",
        content_hash="hash-q-transfer-1", source=DocumentSource.MANUAL,
    )
    session.add(debt_document)
    session.commit()
    session.refresh(debt_document)
    debt_transaction = Transaction(
        document_id=debt_document.id, provider="TRF CRED SEPA+ P/ ANA PEREIRA",
        category=Category.OTHER_EXPENSE, amount=5000.0, currency="EUR",
    )
    session.add(debt_transaction)
    session.commit()
    session.refresh(debt_transaction)

    queue = get_needs_review_queue(session)

    assert unconfirmed.id in [m.id for m in queue.unconfirmed_merchants]
    assert recurring_merchant.id in [m.id for m in queue.recurring_candidates]
    assert debt_transaction.id in [t.id for t in queue.debt_candidates]


def test_get_needs_review_queue_includes_unclassified_transactions(session):
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction
    from app.services.classification_engine import get_needs_review_queue

    document = Document(
        filename="unclassified.pdf", file_path="/tmp/unclassified.pdf",
        content_hash="hash-unclassified-1", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    unclassified_transaction = Transaction(
        document_id=document.id, provider="UNRESOLVABLE PROVIDER",
        category=Category.OTHER, amount=25.0, currency="EUR", merchant_id=None,
    )
    session.add(unclassified_transaction)
    session.commit()
    session.refresh(unclassified_transaction)

    queue = get_needs_review_queue(session)

    assert unclassified_transaction.id in [t.id for t in queue.unclassified_transactions]
