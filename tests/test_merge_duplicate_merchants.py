import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.document import Document, DocumentSource
from app.models.merchant import Merchant
from app.models.transaction import Category, Transaction


def _make_transaction_for_merchant(session, merchant_id, provider, amount=10.0):
    document = Document(
        filename=f"{provider}.pdf", file_path=f"/tmp/{provider}.pdf",
        content_hash=f"hash-merge-{provider}-{merchant_id}", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    transaction = Transaction(
        document_id=document.id, provider=provider, category=Category.GROCERIES,
        amount=amount, currency="EUR", merchant_id=merchant_id,
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)
    return transaction


def test_merge_keeps_survivor_with_most_transactions_and_repoints_others(session, monkeypatch):
    from scripts import merge_duplicate_merchants as merge_script
    monkeypatch.setattr(merge_script, "engine", session.get_bind())

    survivor = Merchant(
        canonical_name="Modelo Hiper", default_category=Category.GROCERIES,
        normalized_key="modelo hiper",
    )
    loser_a = Merchant(
        canonical_name="modelo hiper", default_category=Category.GROCERIES,
        normalized_key="ccr-modelo hiper",
    )
    loser_b = Merchant(
        canonical_name="MODELO HIPER", default_category=Category.GROCERIES,
        normalized_key="compra modelo hiper", confirmed=True, recurring_reviewed=True,
    )
    unrelated = Merchant(
        canonical_name="Continente", default_category=Category.GROCERIES,
        normalized_key="continente",
    )
    session.add_all([survivor, loser_a, loser_b, unrelated])
    session.commit()
    for m in (survivor, loser_a, loser_b, unrelated):
        session.refresh(m)

    for _ in range(3):
        _make_transaction_for_merchant(session, survivor.id, "MODELO HIPER MAFRA")
    _make_transaction_for_merchant(session, loser_a.id, "CCR-MODELO HIPER")
    loser_b_transaction = _make_transaction_for_merchant(session, loser_b.id, "COMPRA MODELO HIPER")
    unrelated_transaction = _make_transaction_for_merchant(session, unrelated.id, "CONTINENTE")

    survivor_id, loser_a_id, loser_b_id, unrelated_id = survivor.id, loser_a.id, loser_b.id, unrelated.id
    loser_b_transaction_id, unrelated_transaction_id = loser_b_transaction.id, unrelated_transaction.id

    merge_script.merge_duplicate_merchants()

    session.expire_all()
    from sqlmodel import select
    remaining_ids = {m.id for m in session.exec(select(Merchant)).all()}
    assert survivor_id in remaining_ids
    assert loser_a_id not in remaining_ids
    assert loser_b_id not in remaining_ids
    assert unrelated_id in remaining_ids

    remaining_survivor = session.exec(select(Merchant).where(Merchant.id == survivor_id)).first()
    assert remaining_survivor.confirmed is True
    assert remaining_survivor.recurring_reviewed is True

    refreshed_loser_b_transaction = session.exec(
        select(Transaction).where(Transaction.id == loser_b_transaction_id)
    ).first()
    assert refreshed_loser_b_transaction.merchant_id == survivor_id
    refreshed_unrelated_transaction = session.exec(
        select(Transaction).where(Transaction.id == unrelated_transaction_id)
    ).first()
    assert refreshed_unrelated_transaction.merchant_id == unrelated_id


def test_merge_is_idempotent(session, monkeypatch):
    from scripts import merge_duplicate_merchants as merge_script
    monkeypatch.setattr(merge_script, "engine", session.get_bind())

    m1 = Merchant(canonical_name="Netflix", default_category=Category.SUBSCRIPTIONS, normalized_key="netflix")
    m2 = Merchant(canonical_name="netflix", default_category=Category.SUBSCRIPTIONS, normalized_key="netflix-2")
    session.add_all([m1, m2])
    session.commit()

    merge_script.merge_duplicate_merchants()
    merge_script.merge_duplicate_merchants()

    from sqlmodel import select
    remaining = session.exec(select(Merchant)).all()
    assert len(remaining) == 1
