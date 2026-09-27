"""The shared fake gateway used by every LLM-touching test module. Records
every request and returns scripted results/errors in order.

Replaces the six ad-hoc Anthropic fakes that existed before the gateway
retrofit. Usage:

    fake = FakeGateway([gateway_text_result('{"a": 1}'), ...])
    # errors can be scripted too:
    fake = FakeGateway(errors=[GatewayError(503, "overloaded")])
    ...
    assert fake.requests[0]["workload_type"] == "classification"
"""

from __future__ import annotations

import copy
import itertools

from app.llm_gateway import GatewayError

_ids = itertools.count(1)
_usage = {"input_tokens": 100, "output_tokens": 20}


def gateway_text_result(text: str) -> dict:
    return {"text": text, "stop_reason": "end_turn", "usage": dict(_usage)}


def gateway_tool_result(tool_calls: list[dict], text: str = "") -> dict:
    return {
        "text": text,
        "stop_reason": "tool_use",
        "usage": dict(_usage),
        "tool_calls": tool_calls,
        "assistant_content": [
            {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c.get("input", {})}
            for c in tool_calls
        ],
    }


def gateway_refusal_result() -> dict:
    return {"text": "", "stop_reason": "refusal", "usage": dict(_usage)}


class FakeGateway:
    """Same `run(...)` signature as GatewayClient; scripts responses in order.
    Entries in `results` may be dicts (bodies) or GatewayError instances."""

    def __init__(self, results: list | None = None, errors: list[GatewayError] | None = None):
        self._bodies = list(results or [])
        self._errors = list(errors or [])
        self.requests: list[dict] = []

    async def run(
        self,
        workload_type: str,
        system: str,
        user: str | None = None,
        *,
        messages: list | None = None,
        tools: list | None = None,
        job_id: str | None = None,
        attachments: list | None = None,
        max_tokens: int | None = None,
        response_schema: dict | None = None,
    ) -> "object":
        self.requests.append({
            "workload_type": workload_type,
            "system": system,
            "user": user,
            "messages": copy.deepcopy(messages),
            "tools": copy.deepcopy(tools),
            "job_id": job_id,
            "attachments": attachments,
            "max_tokens": max_tokens,
            "response_schema": copy.deepcopy(response_schema),
        })
        if self._errors:
            raise self._errors.pop(0)
        if not self._bodies:
            raise AssertionError("FakeGateway ran out of scripted results")
        from app.llm_gateway import GatewayResult

        return GatewayResult(self._bodies.pop(0))
