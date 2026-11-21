"""PLAN step 10g slice 2: the answer card. One plain sentence, one matching visual built from the data the cited tool
read (never from the AI's text, and on the chart rules), sources, Open the page and Save to wiki."""

import json
from datetime import date

import pytest

from app.categories.financials import ask as fin_ask
from app.models.category import Category
from app.models.transaction import Transaction, TransactionType
from app.services.ask.conversation import start_conversation
from app.services.ask.engine import run_turn
from app.services.taxonomy import ensure_taxonomy, file_transaction, get_node
from app.ui import ask_visual
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result, gateway_tool_result
from tests.knowledge_factories import make_document

TODAY = date(2026, 11, 21)


@pytest.fixture()
def groceries(session, monkeypatch):
    monkeypatch.setattr(fin_ask, "_today", lambda: TODAY)
    ensure_taxonomy(session)
    doc = make_document(session, category=Category.FINANCIALS, paper_kind="statement", filename="stmt.pdf")
    leaf = get_node(session, "food.groceries.supermarket")
    for paid, amount in ((date(2026, 9, 5), 100.0), (date(2026, 9, 28), 50.0), (date(2026, 10, 10), 80.0),
                         (date(2026, 10, 25), 40.0), (date(2026, 11, 3), 70.0)):
        t = Transaction(document_id=doc.id, description="CONTINENTE MAFRA", amount=amount, currency="EUR",
                        paid_date=paid, transaction_type=TransactionType.DEBIT)
        session.add(t); session.commit()
        file_transaction(session, t, leaf)
    session.commit()
    return session


async def _answer(session, calls, final):
    gw = FakeGateway([gateway_tool_result([{"id": f"t{i}", "name": n, "input": a} for i, (n, a) in enumerate(calls)]),
                      gateway_text_result(final)])
    turn = start_conversation(session, "How much on groceries?", started_by=None)
    return await run_turn(session, turn.id, gateway=gw, today=TODAY)


MONTHS = ("financials_spending", {"date_from": "2026-09-01", "date_to": "2026-11-30", "group_by": "month",
                                  "category": "food.groceries"})
REF = "financials:spend:food.groceries:2026-09-01:2026-11-30:month"


@pytest.mark.asyncio
async def test_a_monthly_answer_draws_columns_on_the_chart_rules(groceries):
    turn = await _answer(groceries, [MONTHS], f"€70 on groceries so far in November. [[{REF}]]")
    spec = json.loads(turn.visual_json)
    assert spec["kind"] == "columns"
    # A past month: its full month and the part up to the 21st; the current month only what has happened.
    assert spec["items"] == [{"label": "2026-09", "value": 100.0, "full": 150.0},
                             {"label": "2026-10", "value": 80.0, "full": 120.0},
                             {"label": "2026-11", "value": 70.0, "current": True}]
    svg = ask_visual.render(turn.visual_json, json.loads(turn.citations_json))
    assert "<svg" in svg and "€" in svg


@pytest.mark.asyncio
async def test_no_visual_when_the_answer_cites_none_of_the_data(groceries):
    turn = await _answer(groceries, [MONTHS], "I could not tell.")
    assert turn.visual_json is None


@pytest.mark.asyncio
async def test_a_ranking_is_a_table_and_the_card_has_its_buttons(groceries, client):
    ref = "financials:spend:all:2026-09-01:2026-11-30:merchant"
    turn = await _answer(groceries, [("financials_spending", {"date_from": "2026-09-01", "date_to": "2026-11-30",
                                                               "group_by": "merchant"})],
                         f"Continente is where most of it went.\n\nThree months of lines. [[{ref}]]")
    assert json.loads(turn.visual_json)["kind"] == "table"
    page = client.get(f"/ask/c/{turn.conversation_id}").text
    assert '<p class="ask-paragraph lead">Continente is where most of it went.</p>' in page
    assert "€340.00" in page and "Read from 1 source" in page
    assert "Open the page" in page and "/financials/transactions?" in page and "Save to wiki" in page
    assert "chip" not in page.split(f"turn-{turn.id}")[1].split("ask-form")[0]   # no suggested-question chips


@pytest.mark.asyncio
async def test_without_a_chart_the_cited_paper_is_the_visual(session, client):
    doc = make_document(session, category=Category.HOUSE, paper_kind="manual", filename="boiler-manual.pdf")
    turn = await _answer(session, [("find_sources", {"query": "boiler"})], f"The manual is filed. [[doc:{doc.id}]]")
    page = client.get(f"/ask/c/{turn.conversation_id}").text
    assert 'class="ask-visual ask-paper"' in page and "boiler-manual.pdf" in page


def test_one_persons_one_test_is_drawn_with_the_printed_range_and_two_tests_are_not():
    from types import SimpleNamespace as N

    from app.categories.health.ask import _measurement_visual
    def m(v, test="ldl", person=1):
        return N(value=v, person_id=person, test_key=test, test="LDL", ref_low=None, ref_high=115.0)
    rows = [(date(2025, 3, 1), m(130), None), (date(2026, 4, 2), m(110), None)]
    spec = _measurement_visual(rows)
    assert spec["ref"] == [None, 115.0] and [i["value"] for i in spec["items"]] == [130, 110]
    assert spec["items"][-1]["current"] and spec["title"] == "LDL over time"
    assert _measurement_visual(rows + [(date(2026, 5, 1), m(5, test="hdl"), None)]) is None
