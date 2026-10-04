"""The hub's connection to the LLM gateway (llmsel).

Deliberately thin, mirroring eu_track's llmsel_client: it holds no provider
key, names no model, and does not retry — the gateway owns all three. If
this file ever grows a fallback or a retry, the central-brain property has
been lost and the retrofit has failed.
"""

from __future__ import annotations

import httpx

from app.config import settings

_TIMEOUT_S = 600  # long generations are normal; the gateway owns the real budget


# Workload types (D4) — the canonical home for these names; every
# caller (extraction, ask engine, wiki, lint, classifier) imports them
# from here, never from a service module.
VISION_EXTRACTION = "vision_extraction"
VISION_CLASSIFICATION = "vision_classification"
CLASSIFICATION = "classification"
CRITIQUE_REVIEW = "critique_review"
INTERACTIVE_RESEARCH = "interactive_research"


class GatewayError(Exception):
    """The gateway refused or could not serve the call. Never caught here
    and turned into a direct provider call — that is the failure mode this
    project exists to remove."""

    def __init__(self, status: int, detail: str):
        self.status = status  # HTTP status, or 0 when the gateway was unreachable
        self.detail = detail
        super().__init__(detail)

    def __str__(self) -> str:
        # The presentation layer (app.services.presentation) classifies a
        # stored failure_reason by these prefixes: "gateway returned NNN"
        # for HTTP refusals/outages, "gateway unreachable" for transport
        # failures. Keeping the prefix in __str__ is what makes the
        # family-facing messages actually fire in production.
        if self.status == 0:
            return self.detail  # already "gateway unreachable at <url>: ..."
        return f"gateway returned {self.status}: {self.detail}"


class GatewayResult:
    def __init__(self, body: dict):
        self.text: str = body.get("text", "")
        self.stop_reason: str = body.get("stop_reason", "")
        self.usage: dict = body.get("usage", {})
        self.catalog_key: str = body.get("catalog_key", "")
        self.warnings: list = body.get("warnings", [])
        self.tool_calls: list = body.get("tool_calls", [])
        self.assistant_content: list = body.get("assistant_content", [])


class GatewayClient:
    """Async client for `POST {LLMSEL_URL}/run`. `transport` is a test-only
    seam (httpx.MockTransport); the app-level seam is `set_gateway_provider`."""

    def __init__(self, base_url: str, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._transport = transport

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
    ) -> GatewayResult:
        payload: dict = {
            "workload_type": workload_type,
            "system": system,
            "max_tokens": max_tokens,
            "response_schema": response_schema,
        }
        if messages is not None:
            payload["messages"] = messages
            payload["tools"] = tools
            payload["job_id"] = job_id
        else:
            payload["user"] = user
            if attachments is not None:
                payload["attachments"] = attachments
        return GatewayResult(await self._post("/run", payload))

    async def _post(self, path: str, payload: dict) -> dict:
        url = self.base_url + path
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S, transport=self._transport) as http:
                resp = await http.post(
                    url, json=payload,
                    headers={"Authorization": f"Bearer {self._token}"},
                )
        except httpx.HTTPError as exc:
            raise GatewayError(0, f"gateway unreachable at {url}: {exc}") from exc
        if resp.status_code >= 400:
            raise GatewayError(resp.status_code, resp.text[:500])
        return resp.json()


_default_provider = None  # override seam; None -> build a real client from settings


def get_gateway() -> GatewayClient:
    """The injectable seam replacing every `client or AsyncAnthropic(...)`.
    Tests override it via `set_gateway_provider` (or by passing a fake in)."""
    if _default_provider is not None:
        return _default_provider()
    return GatewayClient(settings.LLMSEL_URL, settings.LLMSEL_TOKEN)


def set_gateway_provider(provider) -> None:
    """Override the default provider (pass None to restore the default)."""
    global _default_provider
    _default_provider = provider
