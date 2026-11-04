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
