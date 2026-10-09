import json

import pytest

from app.models.merchant import Merchant
from app.models.transaction import TransactionType
from app.services.classification_engine import classify_transaction, normalize_provider
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy, get_node
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.test_merchant_reconcile import _merchant, _t

SAVINGS = "savings-investments.contributions.deposits-to-savings"
CARD = "internal-transfers.between-my-accounts.top-ups-card-payments"


def _reply(name, slug):
    return FakeGateway([gateway_text_result(json.dumps({"canonical_name": name, "node_slug": slug, "nature": "essential"}))])


_holder_n = [0]


async def _classify(session, description, gw, ttype=TransactionType.DEBIT):
    _holder_n[0] += 1
    holder = _merchant(session, f"holder {_holder_n[0]}")
    t = _t(session, holder, UNSORTED_SLUG, ttype, provider=description)
    t.category_id = None; t.merchant_id = None; session.add(t); session.commit()
    result = await classify_transaction(session, t, gateway=gw)
    return t, result


@pytest.mark.asyncio
async def test_the_prompt_asks_for_the_counterparty_never_the_product_or_action(session):
    ensure_taxonomy(session)
    gw = _reply("Santander", SAVINGS)
    await _classify(session, "SUBSCRIÇÃO SANTANDER AFORROPPR", gw)
    system = gw.requests[0]["system"]
    assert "COUNTERPARTY" in system and "Never the product, fund, action or channel" in system


@pytest.mark.asyncio
async def test_a_multi_purpose_counterparty_is_created_once_and_classifies_by_description(session):
    ensure_taxonomy(session)
    t1, r1 = await _classify(session, "SUBSCRIÇÃO SANTANDER AFORROPPR", _reply("Santander", SAVINGS))
    santander = session.get(Merchant, t1.merchant_id)
    assert r1.created_new_merchant and santander.canonical_name == "Santander" and santander.by_provider is True
    assert t1.category_id == get_node(session, SAVINGS).id

    # another description of the same counterparty: an alias of Santander, filed by ITS category
    t2, r2 = await _classify(session, "PAG.CTA.CARTAO 123", _reply("Santander", CARD))
    assert t2.merchant_id == santander.id and not r2.created_new_merchant
    assert t2.category_id == get_node(session, CARD).id  # not Santander's savings default
    alias = session.query(Merchant).filter(Merchant.normalized_key == normalize_provider("PAG.CTA.CARTAO 123")).one()
    assert alias.merged_into_id == santander.id and session.query(Merchant).filter(Merchant.canonical_name == "Santander").count() == 2

    # the same description again needs no model: the alias resolves to Santander
    t3, _ = await _classify(session, "PAG.CTA.CARTAO 123", FakeGateway([]))
    assert t3.merchant_id == santander.id


@pytest.mark.asyncio
async def test_an_ordinary_counterparty_that_already_exists_is_reused_with_its_own_default(session):
    ensure_taxonomy(session)
    existing = _merchant(session, "Modelo Hiper", "food.groceries.supermarket", confirmed=True)
    t, result = await _classify(session, "MODELO HIPER 99 MAFRA", _reply("Modelo Hiper", "food.groceries.supermarket"))
    assert t.merchant_id == existing.id and not result.created_new_merchant
    assert t.category_id == get_node(session, "food.groceries.supermarket").id


def test_the_chain_rule_names_the_chain_not_the_store():
    from app.services.merchant_rules import keyword_match
    assert keyword_match("MODELO HIPER 2640-MAFR") == ("food.groceries.supermarket", "Modelo Hiper")
    assert keyword_match("PINGO DOCE 1234 LISBOA")[1] == "Pingo Doce"
    assert keyword_match("COMPRA 3315 INTERMARCHE MAFRA")[1] == "Intermarché"
    assert keyword_match("Banco Modelos") is None
