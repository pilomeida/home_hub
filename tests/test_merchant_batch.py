"""PLAN step 9 D9: the new merchants a bank sync brings are asked of the AI together, one call with an index per item,
not one call per line; an item the answer leaves out or gets wrong asks alone, so nothing is lost."""

import json
from datetime import date

from app.models.merchant import Merchant
from app.models.transaction import Transaction
from app.services.bankapi.sync import sync_link
from app.services.classification_engine import classify_transaction, prefetch_merchants
from app.services.taxonomy import get_node
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.test_bank_sync import FakeClient, _run, make_link

SM, REST, FUEL = "food.groceries.supermarket", "food.eat-out.restaurants", "transport.car-running-costs.fuel"


def _row(ref, name, amount="10.00"):
    return {"entry_reference": ref, "status": "BOOK", "credit_debit_indicator": "DBTR",
            "transaction_amount": {"amount": amount, "currency": "EUR"}, "booking_date": "2026-11-18",
            "creditor": {"name": name}}


def _one(name, slug, confidence=0.95):
    return {"canonical_name": name, "node_slug": slug, "nature": "essential", "confidence": confidence}


def _classifier(gw):
    """The production classifier and its prefetch, on a scripted gateway."""
    async def classify(session, transaction, client=None, answers=None):
        return await classify_transaction(session, transaction, gateway=gw, answers=answers)

    async def prefetch(session, lines):
        return await prefetch_merchants(session, lines, gateway=gw)

    classify.prefetch = prefetch
    return classify


def _names(session):
    return {t.description: session.get(Merchant, t.merchant_id).canonical_name for t in session.query(Transaction)}


def test_three_new_merchants_are_one_call_with_an_index_each(payee_rules):
    session = payee_rules
    link, _ = make_link(session)
    rows = [_row("r1", "Tasca do Ze"), _row("r2", "Galp Mafra"), _row("r3", "Cafe Central")]
    gw = FakeGateway([gateway_text_result(json.dumps({"items": [
        {"index": 2, **_one("Café Central", REST)}, {"index": 0, **_one("Tasca do Zé", REST)},
        {"index": 1, **_one("Galp", FUEL)}]}))])
    result = _run(sync_link(session, FakeClient(rows), link, today=date(2026, 11, 20), classify=_classifier(gw)))
    assert result.created == 3 and len(gw.requests) == 1
    request = gw.requests[0]
    assert request["system"].count(SM) == 1                       # the category list is sent once, not per line
    assert "0. 'Tasca do Ze' (a debit)" in request["user"] and "2. 'Cafe Central' (a debit)" in request["user"]
    assert _names(session) == {"Tasca do Ze": "Tasca do Zé", "Galp Mafra": "Galp", "Cafe Central": "Café Central"}
    galp = session.query(Transaction).filter_by(description="Galp Mafra").one()
    assert galp.category_id == get_node(session, FUEL).id


def test_an_item_the_answer_leaves_out_or_gets_wrong_asks_alone(payee_rules):
    session = payee_rules
    link, _ = make_link(session)
    rows = [_row("r1", "Tasca do Ze"), _row("r2", "Galp Mafra"), _row("r3", "Cafe Central")]
    gw = FakeGateway([
        gateway_text_result(json.dumps({"items": [{"index": 0, **_one("Tasca do Zé", REST)},
                                                  {"index": 1, **_one("Galp", "not.a.slug")}]})),
        gateway_text_result(json.dumps(_one("Galp", FUEL))),          # index 1 was wrong: it asks alone
        gateway_text_result(json.dumps(_one("Café Central", REST))),  # index 2 was missing: it asks alone
    ])
    _run(sync_link(session, FakeClient(rows), link, today=date(2026, 11, 20), classify=_classifier(gw)))
    assert len(gw.requests) == 3
    assert "Galp Mafra" in gw.requests[1]["user"] and "Cafe Central" in gw.requests[2]["user"]
    assert _names(session) == {"Tasca do Ze": "Tasca do Zé", "Galp Mafra": "Galp", "Cafe Central": "Café Central"}


def test_an_unreadable_batch_leaves_every_line_to_ask_alone(payee_rules):
    session = payee_rules
    link, _ = make_link(session)
    rows = [_row("r1", "Tasca do Ze"), _row("r2", "Galp Mafra")]
    gw = FakeGateway([gateway_text_result("sorry, no JSON"),
                      gateway_text_result(json.dumps(_one("Tasca do Zé", REST))),
                      gateway_text_result(json.dumps(_one("Galp", FUEL)))])
    result = _run(sync_link(session, FakeClient(rows), link, today=date(2026, 11, 20), classify=_classifier(gw)))
    assert result.created == 2 and result.unclassified == 0 and len(gw.requests) == 3


def test_known_merchants_payees_and_chains_are_not_asked(payee_rules):
    session = payee_rules
    session.add(Merchant(canonical_name="Galp", normalized_key="galp", default_category_id=get_node(session, FUEL).id))
    session.commit()
    link, _ = make_link(session)
    rows = [_row("r1", "Galp Mafra"), _row("r2", "PINGO DOCE MAFRA"), _row("r3", "PAG SERVICOS 1835 27134-627088996"),
            _row("r4", "Tasca do Ze")]
    gw = FakeGateway([gateway_text_result(json.dumps(_one("Tasca do Zé", REST)))])
    _run(sync_link(session, FakeClient(rows), link, today=date(2026, 11, 20), classify=_classifier(gw)))
    assert len(gw.requests) == 1 and "Tasca do Ze" in gw.requests[0]["user"]   # one line left: it asks alone


def test_two_lines_of_one_merchant_are_asked_once(payee_rules):
    session = payee_rules
    lines = [("TASCA DO ZE 2640-MAFR", False), ("TASCA DO ZE MAFRA", False), ("Galp", False)]
    gw = FakeGateway([gateway_text_result(json.dumps({"items": [{"index": 0, **_one("Tasca do Zé", REST)},
                                                               {"index": 1, **_one("Galp", FUEL)}]}))])
    from app.models.category import Category
    from app.privacy.scope import privacy_scope
    with privacy_scope(session, Category.FINANCIALS, "statement"):     # as the sync and the statement reader do
        answers = _run(prefetch_merchants(session, lines, gateway=gw))
    assert len(gw.requests) == 1 and gw.requests[0]["user"].count("TASCA") == 1
    assert set(answers) == {("TASCA DO ZE 2640-MAFR", False), ("Galp", False)}
