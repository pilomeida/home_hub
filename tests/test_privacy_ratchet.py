"""The ratchet (decision D18): once a run has reached a level, no call it makes goes out lower.
Drives the REAL GatewayClient over an httpx.MockTransport, so what is asserted is what would leave the VPS."""

import json

import httpx
import pytest

from app.llm_gateway import GatewayClient
from app.privacy.outbound import Redaction
from app.privacy.ratchet import (
    RatchetLog, current_ratchet, highest, rank, ratchet_scope,
)
from app.privacy.redactor import Redactor
from app.privacy.tiers import Tier
from app.privacy.vault import Vault


class Wire:
    def __init__(self):
        self.bodies: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"text": "ok", "stop_reason": "end_turn", "usage": {}, "catalog_key": "k"})

    def client(self) -> GatewayClient:
        return GatewayClient("http://gw.test:8010", "tok", transport=httpx.MockTransport(self.handler))


def test_rank_orders_none_low_mid_high():
    assert rank(None) < rank(Tier.LOW) < rank(Tier.MID) < rank(Tier.HIGH)
    assert highest([None, Tier.LOW, Tier.HIGH, Tier.MID]) == Tier.HIGH
    assert highest([]) is None


def test_it_only_goes_up_and_records_each_escalation():
    with ratchet_scope(Tier.MID) as r:
        assert r.raise_to(Tier.HIGH, step=2, label="3 Health records") is True
        assert r.raise_to(Tier.MID, step=3, label="x") is False       # never down
        assert r.raise_to(Tier.HIGH, step=3, label="again") is False  # no change, no second record
        assert r.tier == Tier.HIGH and r.private
        assert [(e.step, e.from_tier, e.to_tier, e.label) for e in r.escalations] == \
               [(2, "house_mid", "house_high", "3 Health records")]


def test_a_run_that_starts_high_is_private_without_an_escalation():
    with ratchet_scope(Tier.HIGH) as r:
        assert r.started_private and r.private and r.escalations == []
    log = RatchetLog.from_json(r.to_json())
    assert log.private and log.started_private and log.first_private() is None


def test_log_round_trips_and_garbage_is_private():
    with ratchet_scope(Tier.LOW) as r:
        r.raise_to(Tier.HIGH, step=1, label="a lab report")
    log = RatchetLog.from_json(r.to_json())
    assert log.private and log.first_private().label == "a lab report"
    assert RatchetLog.from_json(None).private is False
    assert RatchetLog.from_json("not json").private is True    # fail closed: a damaged record is private


@pytest.mark.parametrize("raw", ["[]", "null", "42", '"text"'])
def test_valid_json_that_is_not_a_log_reads_as_private(raw):
    assert RatchetLog.from_json(raw).private is True


def test_a_log_with_wrong_field_types_reads_as_private():
    bad = '{"started_private": "no", "escalations": []}'
    assert RatchetLog.from_json(bad).private is True
    missing_key = '{"started_private": false}'
    assert RatchetLog.from_json(missing_key).private is True
    bad_step = ('{"started_private": false, "escalations": [{"step": "2", "from_tier": "house_mid", '
                '"to_tier": "house_high", "label": "x"}]}')
    assert RatchetLog.from_json(bad_step).private is True


def test_an_empty_value_is_an_unused_log_not_private():
    assert RatchetLog.from_json("").private is False
    assert RatchetLog.from_json(None).private is False


def test_the_scope_ends_with_the_block():
    assert current_ratchet() is None
    with ratchet_scope(Tier.MID):
        assert current_ratchet() is not None
    assert current_ratchet() is None


@pytest.mark.asyncio
async def test_without_a_ratchet_a_call_is_untouched():
    wire = Wire()
    await wire.client().run("classification", "sys", "hello")
    assert "sensitivity" not in wire.bodies[0]


@pytest.mark.asyncio
async def test_after_the_ratchet_rises_every_call_goes_out_high_even_when_it_asks_for_less():
    wire = Wire()
    gw = wire.client()
    with ratchet_scope(Tier.MID) as r:
        await gw.run("classification", "sys", "before")                          # no explicit level: the ratchet's Mid
        r.raise_to(Tier.HIGH, step=1, label="private")
        await gw.run("classification", "sys", "after")
        await gw.run("classification", "sys", "explicit low", sensitivity="house_low")
    assert [b.get("sensitivity") for b in wire.bodies] == ["house_mid", "house_high", "house_high"]


@pytest.mark.asyncio
async def test_a_run_that_started_mid_keeps_an_explicit_higher_level():
    wire = Wire()
    with ratchet_scope(Tier.LOW):
        await wire.client().run("classification", "sys", "x", sensitivity="house_mid")
    assert wire.bodies[0]["sensitivity"] == "house_mid"


@pytest.mark.asyncio
async def test_redaction_is_dropped_at_high_because_high_needs_none():
    wire = Wire()
    redaction = Redaction(Redactor({"NAME": ["Ana Exemplo"]}), Vault())
    with ratchet_scope(Tier.HIGH):
        await wire.client().run("classification", "sys", "Ana Exemplo", sensitivity="house_mid", redaction=redaction)
    body = wire.bodies[0]
    assert body["sensitivity"] == "house_high" and "Ana Exemplo" in body["user"]   # sent as is, to the private route only
