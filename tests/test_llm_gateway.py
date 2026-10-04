"""Tests for the hub's connection to the LLM gateway (app/llm_gateway.py)
and the shared fake gateway used by every LLM-touching test module."""

from __future__ import annotations

import json

import httpx
import pytest

from app.llm_gateway import GatewayClient, GatewayError, GatewayResult
from tests.fakes.fake_gateway import (
    FakeGateway,
    gateway_text_result,
    gateway_tool_result,
)


def _client_with_handler(handler, token="tok"):
    transport = httpx.MockTransport(handler)
    return GatewayClient("http://gw.test:8010", token, transport=transport)


@pytest.mark.asyncio
async def test_run_posts_payload_with_bearer_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"text": "hi", "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 2}})

    client = _client_with_handler(handler)
    result = await client.run("classification", "system prompt", "user text")

    assert seen["url"] == "http://gw.test:8010/run"
    assert seen["auth"] == "Bearer tok"
    assert seen["json"] == {
        "workload_type": "classification",
        "system": "system prompt",
        "user": "user text",
        "max_tokens": None,
        "response_schema": None,
    }
    assert isinstance(result, GatewayResult)
    assert result.text == "hi"
    assert result.stop_reason == "end_turn"
    assert result.usage == {"input_tokens": 10, "output_tokens": 2}


@pytest.mark.asyncio
async def test_run_tool_turn_sends_messages_tools_job_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={
            "text": "", "stop_reason": "tool_use",
            "usage": {"input_tokens": 5, "output_tokens": 5},
            "tool_calls": [{"id": "tu_1", "name": "search_wiki", "input": {"q": "x"}}],
            "assistant_content": [{"type": "tool_use", "id": "tu_1", "name": "search_wiki", "input": {"q": "x"}}],
            "job_id": "job-1",
        })

    client = _client_with_handler(handler)
    result = await client.run(
        "interactive_research", "sys", messages=[{"role": "user", "content": "q"}],
        tools=[{"name": "search_wiki"}], job_id="job-1",
    )

    payload = seen["json"]
    assert payload["messages"] == [{"role": "user", "content": "q"}]
    assert payload["tools"] == [{"name": "search_wiki"}]
    assert payload["job_id"] == "job-1"
    assert "user" not in payload
    assert result.tool_calls == [{"id": "tu_1", "name": "search_wiki", "input": {"q": "x"}}]
    assert result.assistant_content[0]["id"] == "tu_1"


@pytest.mark.asyncio
async def test_run_attachments_sent_alongside_user():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"text": "{}", "stop_reason": "end_turn", "usage": {}})

    client = _client_with_handler(handler)
    block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "QUJD"}}
    await client.run("vision_extraction", "sys", "user text", attachments=[block])

    assert seen["json"]["attachments"] == [block]
    assert seen["json"]["user"] == "user text"
    assert "messages" not in seen["json"]


@pytest.mark.asyncio
async def test_non_2xx_raises_gateway_error_with_status_and_detail():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "no model paired for (hub, classification)"})

    client = _client_with_handler(handler)
    with pytest.raises(GatewayError) as excinfo:
        await client.run("classification", "s", "u")
    assert excinfo.value.status == 403
    assert "no model paired" in excinfo.value.detail


@pytest.mark.asyncio
async def test_transport_failure_raises_gateway_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _client_with_handler(handler)
    with pytest.raises(GatewayError) as excinfo:
        await client.run("classification", "s", "u")
    assert excinfo.value.status == 0
    assert "unreachable" in excinfo.value.detail


def test_provider_seam_default_and_override():
    from app import llm_gateway

    # the default provider builds a real client from settings
    default = llm_gateway.get_gateway()
    assert isinstance(default, GatewayClient)
    assert default.base_url == llm_gateway.settings.LLMSEL_URL

    fake = FakeGateway()
    llm_gateway.set_gateway_provider(lambda: fake)
    try:
        assert llm_gateway.get_gateway() is fake
    finally:
        llm_gateway.set_gateway_provider(None)
    assert llm_gateway.get_gateway() is not fake


# --- the shared fake gateway -------------------------------------------


@pytest.mark.asyncio
async def test_fake_gateway_scripts_results_and_records_requests():
    fake = FakeGateway([gateway_text_result('{"a": 1}'), gateway_tool_result([{"id": "tu_9", "name": "t", "input": {}}])])

    r1 = await fake.run("vision_extraction", "sys", "u")
    assert r1.text == '{"a": 1}'
    assert r1.stop_reason == "end_turn"

    r2 = await fake.run("interactive_research", "sys", messages=[{"role": "user", "content": "q"}], tools=[], job_id="j1")
    assert r2.stop_reason == "tool_use"
    assert r2.tool_calls[0]["id"] == "tu_9"

    assert len(fake.requests) == 2
    assert fake.requests[0]["workload_type"] == "vision_extraction"
    assert fake.requests[1]["job_id"] == "j1"


@pytest.mark.asyncio
async def test_fake_gateway_scripts_errors():
    fake = FakeGateway(errors=[GatewayError(status=503, detail="overloaded")])
    with pytest.raises(GatewayError) as excinfo:
        await fake.run("classification", "s", "u")
    assert excinfo.value.status == 503
    assert len(fake.requests) == 1


@pytest.mark.asyncio
async def test_fake_gateway_without_script_raises():
    fake = FakeGateway()
    with pytest.raises(AssertionError):
        await fake.run("classification", "s", "u")
