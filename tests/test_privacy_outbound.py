"""The outbound path (app.llm_gateway.GatewayClient -> /run). Drives the REAL
client against an httpx.MockTransport that records every request body, so
what is asserted is what would actually leave the VPS. Synthetic data only."""

import json

import httpx
import pytest

from app.llm_gateway import GatewayClient, GatewayError
from app.privacy.outbound import Redaction
from app.privacy.redactor import Redactor
from app.privacy.tiers import Tier, TierBlocked
from app.privacy.vault import Vault

IBAN = "PT50003501230001234567818"
NIF = "123456789"
CARD = "4111 1111 1111 1111"
EMAIL = "ana@example.com"
NAME = "Ana Exemplo"
POLICY = "AP-0099887"
SECRETS = [IBAN, NIF, "4111", EMAIL, "Exemplo", POLICY]
KNOWN = {"NAME": [NAME], "POLICY": [POLICY]}
TEXT = f"{NAME}, NIF {NIF}, IBAN {IBAN}, card {CARD}, {EMAIL}, policy {POLICY}. Premium 120,00 EUR."


class Wire:
    """Records what would leave the VPS."""

    def __init__(self, reply="ok [IBAN_1]"):
        self.bodies: list[str] = []
        self.reply = reply

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(request.content.decode())
        return httpx.Response(200, json={"text": self.reply, "stop_reason": "end_turn", "usage": {}, "catalog_key": "k"})

    def client(self) -> GatewayClient:
        return GatewayClient("http://gw.test:8010", "tok", transport=httpx.MockTransport(self.handler))


def _redaction():
    return Redaction(Redactor(KNOWN), Vault())


@pytest.mark.asyncio
async def test_unwired_call_is_untouched_and_carries_no_sensitivity_field():
    wire = Wire()
    await wire.client().run("classification", "sys", TEXT)
    body = json.loads(wire.bodies[0])
    assert "sensitivity" not in body and body["user"] == TEXT      # backward compatible: today's behaviour


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["house_low", "house_mid"])
async def test_tiered_call_tells_the_gateway_its_tier(tier):
    wire = Wire()
    await wire.client().run("classification", "sys", TEXT, sensitivity=tier)
    assert json.loads(wire.bodies[0])["sensitivity"] == tier


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["HIGH", "house_medium", "", "top"])
async def test_unknown_tiers_never_reach_the_wire(bad):
    wire = Wire()
    with pytest.raises(TierBlocked):
        await wire.client().run("classification", "sys", TEXT, sensitivity=bad)
    assert wire.bodies == []                                      # nothing was sent at all


@pytest.mark.asyncio
@pytest.mark.parametrize("high", [Tier.HIGH, "house_high"])
async def test_high_is_sent_to_the_gateway_unredacted_with_its_tier(high):
    """High is served only by the gateway's confidential route (Privatemode); the Hub
    tells the gateway the tier and sends the text as it is (no redaction is needed)."""
    wire = Wire()
    await wire.client().run("classification", "sys", TEXT, sensitivity=high)
    (sent,) = wire.bodies
    assert "house_high" in sent and NAME in sent


@pytest.mark.asyncio
async def test_redaction_removes_every_identifier_from_what_is_sent():
    wire = Wire()
    await wire.client().run("classification", f"You help {NAME}.", TEXT, sensitivity="house_mid",
                            redaction=_redaction())
    (sent,) = wire.bodies
    for secret in SECRETS:
        assert secret not in sent, secret
    assert "120,00 EUR" in sent and "[IBAN_1]" in sent


@pytest.mark.asyncio
async def test_redaction_covers_message_history_text_blocks():
    wire = Wire()
    messages = [{"role": "user", "content": f"my IBAN is {IBAN}"},
                {"role": "assistant", "content": [{"type": "text", "text": f"noted {NAME}"}]}]
    await wire.client().run("interactive_research", "sys", messages=messages, sensitivity="house_mid",
                            redaction=_redaction(), job_id="j1")
    (sent,) = wire.bodies
    assert IBAN not in sent and "Exemplo" not in sent


@pytest.mark.asyncio
async def test_reply_is_restored_locally_after_the_model_answers():
    wire = Wire(reply="Your account is [IBAN_1].")
    result = await wire.client().run("classification", "sys", f"IBAN {IBAN}", sensitivity="house_mid",
                                     redaction=_redaction())
    assert result.text == f"Your account is {IBAN}."
    assert IBAN not in wire.bodies[0]


@pytest.mark.asyncio
async def test_redaction_with_attachments_is_blocked_not_half_redacted():
    wire = Wire()
    pdf = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "AAAA"}}]
    with pytest.raises(TierBlocked, match="attachments"):
        await wire.client().run("vision_extraction", "sys", "read it", attachments=pdf,
                                sensitivity="house_mid", redaction=_redaction())
    assert wire.bodies == []


@pytest.mark.asyncio
async def test_redaction_with_images_in_history_is_blocked():
    wire = Wire()
    messages = [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA"}}]}]
    with pytest.raises(TierBlocked):
        await wire.client().run("interactive_research", "sys", messages=messages, job_id="j",
                                sensitivity="house_mid", redaction=_redaction())
    assert wire.bodies == []


@pytest.mark.asyncio
async def test_redaction_with_tools_is_blocked():
    wire = Wire()
    with pytest.raises(TierBlocked, match="tool"):
        await wire.client().run("interactive_research", "sys", messages=[{"role": "user", "content": "hi"}],
                                tools=[{"name": "t"}], job_id="j", sensitivity="house_mid", redaction=_redaction())
    assert wire.bodies == []


@pytest.mark.asyncio
async def test_a_failing_redactor_blocks_the_call():
    class Broken(Redactor):
        def find(self, text):
            raise RuntimeError("boom")

    wire = Wire()
    with pytest.raises(TierBlocked):
        await wire.client().run("classification", "sys", TEXT, sensitivity="house_mid",
                                redaction=Redaction(Broken(), Vault()))
    assert wire.bodies == []


@pytest.mark.asyncio
async def test_redaction_without_a_tier_is_refused():
    wire = Wire()
    with pytest.raises(TierBlocked):
        await wire.client().run("classification", "sys", TEXT, redaction=_redaction())
    assert wire.bodies == []


@pytest.mark.asyncio
async def test_tripwire_no_identifier_reaches_the_wire_in_any_redacted_combination():
    """Whatever the tier or call shape, if redaction was requested no synthetic
    identifier may appear in any request body."""
    wire = Wire()
    for tier in ("house_low", "house_mid"):
        for system in ("sys", f"for {NAME} {IBAN}"):
            await wire.client().run("classification", system, TEXT, sensitivity=tier, redaction=_redaction())
    assert len(wire.bodies) == 4
    for sent in wire.bodies:
        for secret in SECRETS:
            assert secret not in sent, secret


@pytest.mark.asyncio
async def test_gateway_403_sensitivity_blocked_surfaces_as_a_gateway_error():
    def handler(request):
        return httpx.Response(403, json={"detail": "sensitivity house_high has no route yet", "code": "sensitivity_blocked"})

    client = GatewayClient("http://gw.test:8010", "tok", transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayError, match="403"):
        await client.run("classification", "sys", "x", sensitivity="house_mid")


@pytest.mark.asyncio
async def test_audit_log_has_counts_and_sizes_but_never_content(caplog):
    wire = Wire()
    with caplog.at_level("INFO", logger="home_hub.privacy"):
        await wire.client().run("classification", "sys", TEXT, sensitivity="house_mid", redaction=_redaction())
        with pytest.raises(TierBlocked):
            await wire.client().run("classification", "sys", TEXT, sensitivity="house_medium")
    log = "\n".join(r.getMessage() for r in caplog.records)
    assert "house_mid" in log and "'redacted': True" in log and "'outcome': 'blocked'" in log
    for secret in SECRETS + ["120,00"]:
        assert secret not in log, secret
