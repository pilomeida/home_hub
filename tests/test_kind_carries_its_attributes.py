"""Work-list B2: a paper kind carries what the model puts on it, declared on its spec (KindSpec), so adding a kind
is a spec change only. Slice 1: its privacy level."""

import dataclasses

import pytest

from app.categories.base import KindSpec
from app.categories.registry import get_spec, implemented_categories
from app.models.category import Category
from app.privacy import settings as ps
from app.privacy.tiers import Tier


def _with_extra_kind(monkeypatch, category, kind):
    spec = get_spec(category)
    grown = dataclasses.replace(spec, kinds=spec.kinds + (kind,))
    monkeypatch.setattr(ps, "implemented_categories", lambda: [grown if s.category == category else s
                                                              for s in implemented_categories()])


def test_a_new_kind_brings_its_suggested_level_with_it(monkeypatch):
    _with_extra_kind(monkeypatch, Category.HOUSE, KindSpec("garden_plan", "Garden plan", "A plan of the garden.",
                                                         suggested_tier=Tier.LOW))
    info = {(i.category, i.paper_kind): i for i in ps.known_types()}
    assert info[("house", "garden_plan")].suggested == Tier.LOW
    assert ps.suggested_tiers()[("house", "garden_plan")] == Tier.LOW


def test_a_kind_that_declares_no_level_is_offered_the_cautious_middle(monkeypatch):
    _with_extra_kind(monkeypatch, Category.HOUSE, KindSpec("garden_plan", "Garden plan", "A plan of the garden."))
    info = {(i.category, i.paper_kind): i for i in ps.known_types()}
    assert info[("house", "garden_plan")].suggested == ps.FALLBACK_SUGGESTION == Tier.MID


def test_high_is_never_suggested_it_is_chosen():
    with pytest.raises(ValueError, match="High is never suggested"):
        KindSpec("x", "X", "x", suggested_tier=Tier.HIGH)


def test_a_new_health_kind_is_high_whatever_it_declares(monkeypatch):
    _with_extra_kind(monkeypatch, Category.HEALTH, KindSpec("dental_xray", "Dental X-ray", "An X-ray of the teeth.",
                                                          suggested_tier=Tier.LOW))
    info = {(i.category, i.paper_kind): i for i in ps.known_types()}
    assert info[("health", "dental_xray")].suggested == Tier.HIGH
    assert ps.forced_tier(Category.HEALTH) == Tier.HIGH


@pytest.mark.asyncio
async def test_the_health_reader_asks_for_healths_declared_level(tmp_path):
    """What goes over the wire to the gateway is the level Health declares (one source, no copy in the reader)."""
    import json
    from app.categories.health.extraction import extract_health_document
    from tests import privacy_world as pw
    from tests.fakes.fake_gateway import gateway_text_result
    from tests.test_health_no_billing import REPLY, _png
    wire = pw.Wire(script=[gateway_text_result(json.dumps(REPLY))])
    await extract_health_document(_png(tmp_path), "lab_result", gateway=wire.client())
    assert wire.parsed()[-1]["sensitivity"] == get_spec(Category.HEALTH).forced_tier.value == "house_high"


# --- B2's done-when: adding a kind is a spec change only, shown with one existing kind ----------------------------

@pytest.mark.asyncio
async def test_a_new_money_kind_is_a_spec_change_only(session, monkeypatch, tmp_path):
    """A card statement declared on Money's spec, reusing the statement's reader: it is read into bank lines (with
    the statement's add-up), offered its level, and a delete knows what it takes back. No handler is touched."""
    from datetime import date
    from sqlmodel import select
    from app.categories import registry
    from app.categories.base import FiledAs
    from app.categories.financials import handler as money
    from app.models.document import DocumentStatus
    from app.models.transaction import StatementCheck, Transaction
    from app.services.extraction import ExtractedStatement, ExtractedTransaction
    from app.services.paper_uses import filed_as
    from tests.test_financials_handler import _make_document

    card = KindSpec("card_statement", "Card statement", "A credit card's monthly statement.", suggested_tier=Tier.MID,
                    reader=money.read_statement, files_as=frozenset({FiledAs.MONEY}))
    spec = get_spec(Category.FINANCIALS)
    grown = dataclasses.replace(spec, kinds=spec.kinds + (card,))
    specs = {**registry._specs(), Category.FINANCIALS: grown}
    monkeypatch.setattr(registry, "_specs", lambda: specs)

    async def read(file_path, gateway=None):            # the statement reader's model call, as the bill tests fake it
        return ExtractedStatement(statement_period="2026-10", transactions=[ExtractedTransaction(
            transaction_date=date(2026, 10, 3), description="GALP MAFRA", amount=61.2, currency="EUR",
            transaction_type="debit", category_hint="other_expense")])
    monkeypatch.setattr(money, "extract_statement_transactions", read)

    document = _make_document(session, tmp_path, filename="card-2026-10.pdf", content_hash="card-1")
    document.category, document.paper_kind = Category.FINANCIALS, "card_statement"
    session.add(document); session.commit()

    result = await money.process_financials_document(session, document)

    assert result.status == DocumentStatus.PROCESSED
    lines = session.exec(select(Transaction).where(Transaction.document_id == document.id)).all()
    assert [(t.description, t.amount) for t in lines] == [("GALP MAFRA", 61.2)]
    assert session.exec(select(StatementCheck).where(StatementCheck.document_id == document.id)).first() is not None
    assert filed_as(result) == {"money"}
    assert {(i.category, i.paper_kind): i.suggested for i in ps.known_types()}[("financials", "card_statement")] == Tier.MID


def test_no_handler_branches_on_the_kind_when_reading():
    """The readers are chosen from the spec (read_by_kind); a handler's process never tests the kind itself."""
    import ast
    import inspect
    from app.categories.registry import implemented_categories
    for spec in implemented_categories():
        tree = ast.parse(inspect.getsource(type(spec.handler)))
        process = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "process")
        assert "paper_kind ==" not in ast.unparse(process) and "paper_kind in" not in ast.unparse(process), spec.label
