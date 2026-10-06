import json

import pytest

from app.models.transaction import Category, Nature
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy, get_node
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


def _fake_merchant_gateway(response_text: str):
    """A FakeGateway scripted with one merchant-resolution JSON result."""
    from tests.fakes.fake_gateway import FakeGateway

    return FakeGateway([{
        "text": response_text, "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }])


@pytest.mark.asyncio
async def test_resolve_merchant_via_llm_parses_valid_response(session):
    ensure_taxonomy(session)
    response = json.dumps({
        "canonical_name": "Modelo Hiper",
        "node_slug": "food.groceries.supermarket",
        "nature": "essential",
    })
    client = _fake_merchant_gateway(response)

    result = await resolve_merchant_via_llm("MODELO HIPER 2640-MAFR", session, gateway=client)

    assert isinstance(result, ResolvedMerchant)
    assert result.canonical_name == "Modelo Hiper"
    assert result.node_slug == "food.groceries.supermarket"
    assert result.category == Category.GROCERIES
    assert result.nature == Nature.ESSENTIAL
    # the prompt lists the leaf slugs and the schema enum is exactly that list,
    # minus the nodes only the loan-insurance linker may use
    from app.services.taxonomy import leaf_slugs
    req = client.requests[0]
    assert req["response_schema"]["properties"]["node_slug"]["enum"] == [
        s for s in leaf_slugs(session) if not s.startswith("loans-debt.loan-insurance.")]
    assert "food.groceries.supermarket" in req["system"]
    assert UNSORTED_SLUG in req["system"]


@pytest.mark.asyncio
async def test_resolve_merchant_via_llm_raises_on_malformed_json(session):
    ensure_taxonomy(session)
    client = _fake_merchant_gateway("not json")

    with pytest.raises(MerchantResolutionError):
        await resolve_merchant_via_llm("SOME PROVIDER", session, gateway=client)


@pytest.mark.asyncio
async def test_resolve_merchant_via_llm_raises_on_invalid_category(session):
    ensure_taxonomy(session)
    response = json.dumps({
        "canonical_name": "Some Shop", "node_slug": "made.up.slug", "nature": "essential",
    })
    client = _fake_merchant_gateway(response)

    with pytest.raises(MerchantResolutionError):
        await resolve_merchant_via_llm("SOME SHOP", session, gateway=client)


@pytest.mark.asyncio
async def test_resolve_merchant_via_llm_raises_when_taxonomy_not_seeded(session):
    client = _fake_merchant_gateway(json.dumps({
        "canonical_name": "X", "node_slug": UNSORTED_SLUG, "nature": "essential"}))
    with pytest.raises(MerchantResolutionError):
        await resolve_merchant_via_llm("X", session, gateway=client)


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

    result = await classify_transaction(session, transaction, gateway=_fake_merchant_gateway("{}"))
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

    ensure_taxonomy(session)

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
        "canonical_name": "Loja Nova", "node_slug": "personal-lifestyle.personal.general-shopping", "nature": "discretionary",
    })
    result = await classify_transaction(session, transaction, gateway=_fake_merchant_gateway(response))
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

    await classify_transaction(session, transaction, gateway=_fake_merchant_gateway("{}"))
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

    await classify_transaction(session, transaction, gateway=_fake_merchant_gateway("{}"))
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

    await classify_transaction(session, transaction, gateway=_fake_merchant_gateway("{}"))
    session.commit()

    assert transaction.nature == Nature.DISCRETIONARY


@pytest.mark.asyncio
async def test_classify_transaction_does_not_commit_internally(session):
    from app.models.document import Document, DocumentSource
    from app.models.merchant import Merchant
    from app.models.transaction import Transaction
    from sqlmodel import select

    ensure_taxonomy(session)

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
        "canonical_name": "Loja Nova", "node_slug": "personal-lifestyle.personal.general-shopping", "nature": "discretionary",
    })
    result = await classify_transaction(session, transaction, gateway=_fake_merchant_gateway(response))
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


def _txn(session, provider, ttype=None, amount=15.0, hash_=None):
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction, TransactionType

    document = Document(filename=f"{provider}.pdf", file_path=f"/tmp/{provider}.pdf",
                        content_hash=hash_ or f"h5-{provider}", source=DocumentSource.MANUAL)
    session.add(document)
    session.commit()
    t = Transaction(document_id=document.id, provider=provider, category=Category.OTHER,
                    transaction_type=ttype or TransactionType.DEBIT, amount=amount, currency="EUR")
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _reply(slug, name="Galp", nature="essential"):
    return _fake_merchant_gateway(json.dumps(
        {"canonical_name": name, "node_slug": slug, "nature": nature}))


@pytest.mark.asyncio
async def test_classify_files_new_merchant_under_tree_leaf(session):
    ensure_taxonomy(session)
    t = _txn(session, "GALP LISBOA")
    result = await classify_transaction(
        session, t, gateway=_reply("transport.car-running.fuel"))
    fuel = get_node(session, "transport.car-running.fuel")
    assert result.merchant.default_category_id == fuel.id
    assert t.category_id == fuel.id
    assert t.category == Category.OTHER_EXPENSE  # dual-write via kind default (fuel has no legacy value)


@pytest.mark.asyncio
async def test_classify_dual_writes_legacy_on_merchant_and_transaction(session):
    ensure_taxonomy(session)
    t = _txn(session, "MODELO")
    result = await classify_transaction(
        session, t, gateway=_reply("food.groceries.supermarket", "Modelo"))
    assert t.category == Category.GROCERIES
    assert result.merchant.default_category == Category.GROCERIES


@pytest.mark.asyncio
async def test_classify_unsorted_merchant_leaves_transaction_in_review_queue(session):
    from app.services.classification_engine import get_needs_review_queue

    ensure_taxonomy(session)
    t = _txn(session, "MYSTERY CO")
    result = await classify_transaction(session, t, gateway=_reply(UNSORTED_SLUG, "Mystery Co"))
    unsorted = get_node(session, UNSORTED_SLUG)
    assert result.merchant.default_category_id == unsorted.id
    assert t.category_id == unsorted.id  # production files it explicitly under Unsorted
    session.commit()
    queue = get_needs_review_queue(session)
    assert t.id in [x.id for x in queue.unsorted_transactions]


@pytest.mark.asyncio
async def test_classify_existing_merchant_with_node_files_new_transaction(session):
    from app.models.merchant import Merchant

    ensure_taxonomy(session)
    fuel = get_node(session, "transport.car-running.fuel")
    session.add(Merchant(canonical_name="Galp", normalized_key="galp", default_category_id=fuel.id,
                         default_nature=Nature.ESSENTIAL))
    session.commit()
    t = _txn(session, "GALP")
    await classify_transaction(session, t, gateway=_fake_merchant_gateway("{}"))  # no LLM needed
    assert t.category_id == fuel.id


@pytest.mark.asyncio
async def test_classify_existing_merchant_without_node_is_not_filed(session):
    from app.models.merchant import Merchant

    ensure_taxonomy(session)
    session.add(Merchant(canonical_name="Old", normalized_key="old", default_category=Category.GROCERIES))
    session.commit()
    t = _txn(session, "OLD")
    await classify_transaction(session, t, gateway=_fake_merchant_gateway("{}"))
    assert t.category_id is None


def test_get_needs_review_queue_groups_unfiled_and_unsorted_by_merchant(session):
    from app.models.merchant import Merchant
    from app.services.classification_engine import get_needs_review_queue
    from app.services.taxonomy import file_transaction

    ensure_taxonomy(session)
    unsorted = get_node(session, UNSORTED_SLUG)
    fuel = get_node(session, "transport.car-running.fuel")
    m = Merchant(canonical_name="Shop", normalized_key="shop")
    session.add(m)
    session.commit()
    a, b, c = _txn(session, "SHOP A"), _txn(session, "SHOP B"), _txn(session, "SHOP C")
    a.merchant_id = b.merchant_id = c.merchant_id = m.id
    file_transaction(session, b, unsorted)
    file_transaction(session, c, fuel)
    session.commit()

    queue = get_needs_review_queue(session)
    ids = [t.id for t in queue.unsorted_transactions]
    assert a.id in ids and b.id in ids and c.id not in ids
    group = queue.unsorted_by_merchant[m.id]
    assert sorted(t.id for t in group) == sorted([a.id, b.id])


@pytest.mark.asyncio
async def test_merchant_memory_does_not_file_a_credit_under_an_out_node(session):
    from app.models.merchant import Merchant
    from app.models.transaction import TransactionType
    from app.services.classification_engine import get_needs_review_queue

    ensure_taxonomy(session)
    sm = get_node(session, "food.groceries.supermarket")
    session.add(Merchant(canonical_name="Modelo", normalized_key="modelo", default_category_id=sm.id))
    session.commit()
    refund = _txn(session, "MODELO", ttype=TransactionType.CREDIT, hash_="h-refund")
    debit = _txn(session, "MODELO", ttype=TransactionType.DEBIT, hash_="h-debit")
    await classify_transaction(session, refund, gateway=_fake_merchant_gateway("{}"))
    await classify_transaction(session, debit, gateway=_fake_merchant_gateway("{}"))
    session.commit()
    assert debit.category_id == sm.id
    assert refund.category_id is None
    assert refund.id in [t.id for t in get_needs_review_queue(session).unsorted_transactions]


@pytest.mark.asyncio
async def test_new_merchant_credit_with_out_node_is_not_filed(session):
    from app.models.transaction import TransactionType

    ensure_taxonomy(session)
    t = _txn(session, "ODD CREDIT", ttype=TransactionType.CREDIT)
    await classify_transaction(session, t, gateway=_reply("food.groceries.supermarket", "Odd"))
    assert t.category_id is None


def test_direction_matches_rules(session):
    from app.models.transaction import Transaction, TransactionType
    from app.services.taxonomy import direction_matches

    ensure_taxonomy(session)
    out = get_node(session, "food.groceries.supermarket")
    unsorted = get_node(session, UNSORTED_SLUG)
    refunds = get_node(session, "refunds-reimbursements.refunds.purchase-refunds")
    def tx(k): return Transaction(document_id=1, provider="x", amount=1, transaction_type=k)
    assert direction_matches(tx(TransactionType.DEBIT), out)
    assert not direction_matches(tx(TransactionType.TRANSFER), out)
    assert not direction_matches(tx(TransactionType.TRANSFER), refunds)
    internal = get_node(session, "internal-transfers.between-my-accounts.santander-revolut")
    assert direction_matches(tx(TransactionType.TRANSFER), internal)
    assert direction_matches(tx(TransactionType.TRANSFER), unsorted)
    assert not direction_matches(tx(TransactionType.CREDIT), out)
    assert direction_matches(tx(TransactionType.CREDIT), refunds)
    assert not direction_matches(tx(TransactionType.DEBIT), refunds)
    assert direction_matches(tx(TransactionType.CREDIT), unsorted)
    assert direction_matches(tx(TransactionType.DEBIT), unsorted)


def test_prompt_names_the_credit_only_prefixes_and_not_loan_repayments(session):
    import asyncio
    from app.services.taxonomy import leaf_slugs

    ensure_taxonomy(session)
    gw = _reply(UNSORTED_SLUG)
    asyncio.run(resolve_merchant_via_llm("X", session, gateway=gw))
    system = gw.requests[0]["system"]
    for prefix in ("income.", "refunds-reimbursements.", "loans-debt-in."):
        assert prefix in system
        assert any(sl.startswith(prefix) for sl in leaf_slugs(session))
    assert any(sl.startswith("loans-debt.") for sl in leaf_slugs(session))
    credit_line = next(l for l in system.splitlines() if l.startswith("Credit-only"))
    debit_line = next(l for l in system.splitlines() if l.startswith("Debit-only"))
    assert "loans-debt." not in credit_line.replace("loans-debt-in.", "")
    assert "loans-debt." in debit_line


# ---- loan-insurance nodes are reachable only through insurance linking ----

INSURANCE_SLUGS = ("loans-debt.loan-insurance.life-insurance-loan", "loans-debt.loan-insurance.building-insurance-loan")


@pytest.mark.asyncio
async def test_prompt_and_schema_hide_loan_insurance_but_keep_other_loan_slugs(session):
    ensure_taxonomy(session)
    gw = _reply(UNSORTED_SLUG)
    await resolve_merchant_via_llm("X", session, gateway=gw)
    request = gw.requests[0]
    enum = request["response_schema"]["properties"]["node_slug"]["enum"]
    for slug in INSURANCE_SLUGS:
        assert slug not in enum and slug not in request["system"]
    assert "loans-debt.loan-repayments.mortgage" in enum and "loans-debt.interest-fees.loan-interest" in enum


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", INSURANCE_SLUGS)
async def test_llm_reply_with_a_loan_insurance_slug_is_rejected(session, slug):
    ensure_taxonomy(session)
    with pytest.raises(MerchantResolutionError):
        await resolve_merchant_via_llm("GENERIC INSURER", session, gateway=_reply(slug))


# --- informal loans: DebtMatchRule auto-link creates ledger entries -------

async def _classify_with_rule(session, direction, ttype, amount=100.0):
    from datetime import date
    from decimal import Decimal  # noqa: F401
    from app.models.document import Document, DocumentSource
    from app.models.merchant import Merchant
    from app.models.position import DebtMatchRule
    from app.models.transaction import Transaction
    from app.services.debt_ledger import create_informal_debt

    ensure_taxonomy(session)
    provider = "TRF SEPA+ P/ TEST PERSON"
    debt = create_informal_debt(session, "Test Person", direction, opening_amount=1000.0)
    session.add(DebtMatchRule(debt_id=debt.id, normalized_key=normalize_provider(provider)))
    session.add(Merchant(canonical_name="Test Person", default_category=Category.OTHER_EXPENSE,
                         default_nature=Nature.ESSENTIAL, normalized_key=normalize_provider(provider),
                         default_category_id=get_node(session, UNSORTED_SLUG).id))
    doc = Document(filename="ac.pdf", file_path="/tmp/ac.pdf", content_hash=f"ac-{direction}-{ttype}", source=DocumentSource.MANUAL)
    session.add(doc)
    session.commit()
    txn = Transaction(document_id=doc.id, provider=provider, amount=amount, transaction_type=ttype,
                      category=Category.OTHER_EXPENSE, paid_date=date(2026, 2, 1))
    session.add(txn)
    session.commit()
    await classify_transaction(session, txn, gateway=_fake_merchant_gateway("{}"))
    session.commit()
    session.refresh(txn)
    session.refresh(debt)
    return debt, txn


@pytest.mark.asyncio
async def test_match_rule_auto_link_creates_a_repayment_entry_and_files_it(session):
    from decimal import Decimal
    from sqlmodel import select
    from app.models.category_node import CategoryNode
    from app.models.debt import DebtDirection
    from app.models.position import DebtEntry
    from app.models.transaction import TransactionType

    debt, txn = await _classify_with_rule(session, DebtDirection.OWED_TO_US, TransactionType.CREDIT)
    assert txn.debt_id == debt.id and txn.debt_candidate_reviewed is True
    entry = session.exec(select(DebtEntry).where(DebtEntry.transaction_id == txn.id)).one()
    assert entry.kind == "repayment" and entry.amount == 100.0
    assert debt.current_balance == Decimal("900.00")
    # loan filing wins over the merchant's Unsorted memory
    assert session.get(CategoryNode, txn.category_id).slug == "loans-debt-in.repayments-received.from-friends"


@pytest.mark.asyncio
async def test_match_rule_auto_link_borrowed_debit_is_a_repayment(session):
    from decimal import Decimal
    from app.models.debt import DebtDirection
    from app.models.transaction import TransactionType

    debt, txn = await _classify_with_rule(session, DebtDirection.OWED_BY_US, TransactionType.DEBIT, 250.0)
    assert txn.debt_id == debt.id
    assert debt.current_balance == Decimal("750.00")


@pytest.mark.asyncio
async def test_match_rule_skips_transfer_type_transactions(session):
    from decimal import Decimal
    from sqlmodel import select
    from app.models.debt import DebtDirection
    from app.models.position import DebtEntry
    from app.models.transaction import TransactionType

    debt, txn = await _classify_with_rule(session, DebtDirection.OWED_TO_US, TransactionType.TRANSFER)
    assert txn.debt_id is None and not txn.debt_candidate_reviewed  # stays in Needs Review
    assert len(session.exec(select(DebtEntry)).all()) == 1  # only the opening entry
    assert debt.current_balance == Decimal("1000.00")
