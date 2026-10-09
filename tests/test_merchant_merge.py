import pytest
from sqlmodel import select

from app.models.merchant import Merchant
from app.services.classification_engine import classify_transaction, get_needs_review_queue, normalize_provider
from app.services.merchant_merge import merge_merchants, resolve_merchant
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy
from tests.test_merchant_reconcile import _merchant, _t

PROVIDER = "SUBSCRIÇÃO SANTANDER AFORROPPR"  # as it sits in the live statements


def _three(session):
    ensure_taxonomy(session)
    sub = _merchant(session, "Santander Subscription")
    aforro = _merchant(session, "Santander Aforro")
    ppr = _merchant(session, "Santander Aforro PPR", confirmed=True)
    rows = [_t(session, m, UNSORTED_SLUG, provider=PROVIDER) for m in (sub, sub, aforro, ppr)]
    return sub, aforro, ppr, rows


def test_merge_moves_the_entries_keeps_the_rows_and_can_rename_the_survivor(session):
    sub, aforro, ppr, rows = _three(session)
    report = merge_merchants(session, [sub.id, aforro.id], ppr.id, new_name="Santander Aforro PPR (savings)")
    session.expire_all()
    assert (report.merchants_merged, report.transactions_moved) == (2, 3)
    assert {session.get(type(rows[0]), r.id).merchant_id for r in rows} == {ppr.id}
    assert session.get(Merchant, sub.id).merged_into_id == ppr.id and session.get(Merchant, aforro.id).merged_into_id == ppr.id
    assert session.get(Merchant, ppr.id).canonical_name == "Santander Aforro PPR (savings)"
    assert merge_merchants(session, [sub.id], ppr.id).transactions_moved == 0  # idempotent


def test_dry_run_writes_nothing_and_chains_are_flattened(session):
    sub, aforro, ppr, rows = _three(session)
    merge_merchants(session, [sub.id], ppr.id, dry_run=True)
    session.expire_all()
    assert session.get(Merchant, sub.id).merged_into_id is None
    merge_merchants(session, [sub.id], aforro.id)
    merge_merchants(session, [aforro.id], ppr.id)
    session.expire_all()
    assert session.get(Merchant, sub.id).merged_into_id == ppr.id  # not via Aforro
    assert resolve_merchant(session, session.get(Merchant, sub.id)).id == ppr.id


@pytest.mark.asyncio
async def test_the_same_bank_text_arriving_later_lands_on_the_survivor_without_the_model(session):
    from tests.fakes.fake_gateway import FakeGateway
    ensure_taxonomy(session)
    old = Merchant(canonical_name="Santander Subscription", normalized_key=normalize_provider(PROVIDER))
    survivor = _merchant(session, "Santander Aforro PPR", confirmed=True)
    session.add(old); session.commit()
    merge_merchants(session, [old.id], survivor.id)
    new = _t(session, survivor, UNSORTED_SLUG, provider=PROVIDER)
    new.merchant_id = None; session.add(new); session.commit()
    gw = FakeGateway([])
    await classify_transaction(session, new, gateway=gw)
    assert gw.requests == [] and new.merchant_id == survivor.id


def test_a_merged_merchant_is_not_offered_for_review(session):
    sub, aforro, ppr, rows = _three(session)
    merge_merchants(session, [sub.id, aforro.id], ppr.id)
    names = {m.canonical_name for m in get_needs_review_queue(session).unconfirmed_merchants}
    assert "Santander Subscription" not in names and "Santander Aforro" not in names
