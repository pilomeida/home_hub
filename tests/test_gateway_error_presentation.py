"""Gateway-failure presentation tests: each reason is built the way
production builds it — a real GatewayError raised by the gateway client /
fake gateway and stored by the real ingestion or handler code path — then
run through the presentation helpers. No reason string is ever hand-typed."""

import pytest

from app.llm_gateway import GatewayClient, GatewayError
from app.models.document import DocumentSource
from app.services import ingestion, presentation
from tests.domain_fakes import RecordingHandler, make_fake_spec


def _client_with_handler(handler):
    import httpx

    transport = httpx.MockTransport(handler)
    return GatewayClient("http://gw.test:8010", "tok", transport=transport)


def _reason_from_gateway_status(status: int, body: dict) -> str:
    """Raise the way the real client raises on an HTTP error and capture
    the string that the ingestion boundary stores as failure_reason."""
    def handler(request):
        from httpx import Response

        return Response(status, json=body)

    client = _client_with_handler(handler)
    try:
        import asyncio

        asyncio.run(client.run("classification", "s", "u"))
    except GatewayError as exc:
        return str(exc)
    raise AssertionError("expected a GatewayError")


def _reason_from_transport_failure() -> str:
    def handler(request):
        raise __import__("httpx").ConnectError("connection refused")

    client = _client_with_handler(handler)
    try:
        import asyncio

        asyncio.run(client.run("classification", "s", "u"))
    except GatewayError as exc:
        return str(exc)
    raise AssertionError("expected a GatewayError")


async def _stored_reason_via_ingestion(session, monkeypatch, exc) -> str:
    """Run a real finalize_document whose handler raises `exc`; return the
    failure_reason the ingestion boundary actually stored."""
    from app.domains import registry

    spec = make_fake_spec(handler=RecordingHandler(fail_with=exc))
    monkeypatch.setattr(registry, "_specs_cache", {spec.domain: spec})
    incoming = ingestion.IncomingFile(
        filename="doc.pdf", content=b"pdf-bytes",
        source=DocumentSource.MANUAL, uploaded_by="pedro@example.com",
    )
    result = await ingestion.ingest(
        session, incoming,
        ingestion.Classification(spec.domain, "manual", {"item_name": "Boiler"}),
    )
    document = result.document
    assert document.status.value == "needs_attention"
    return document.failure_reason


@pytest.mark.asyncio
async def test_gateway_5xx_reason_is_mapped_to_outage_message(session, monkeypatch):
    reason = await _stored_reason_via_ingestion(
        session, monkeypatch, GatewayError(503, '{"detail": "all suppliers failing"}')
    )
    # The stored text is what the ingestion boundary wrote: its own prefix
    # around str(GatewayError) — which now carries "gateway returned NNN".
    assert reason.startswith("Fake processing failed: gateway returned 503: ")
    assert presentation.humanize_reason_text(reason) == "The AI service couldn't be reached."


@pytest.mark.asyncio
async def test_gateway_4xx_reason_is_mapped_to_read_failure_message(session, monkeypatch):
    reason = await _stored_reason_via_ingestion(
        session, monkeypatch, GatewayError(422, '{"detail": "no model paired for (hub, vision_extraction)"}')
    )
    assert reason.startswith("Fake processing failed: gateway returned 422: ")
    assert presentation.humanize_reason_text(reason) == "This document couldn't be read automatically."


@pytest.mark.asyncio
async def test_gateway_transport_reason_is_mapped_to_outage_message(session, monkeypatch):
    reason = await _stored_reason_via_ingestion(
        session, monkeypatch, GatewayError(0, "gateway unreachable at http://127.0.0.1:8010/run: connection refused")
    )
    assert reason == "Fake processing failed: gateway unreachable at http://127.0.0.1:8010/run: connection refused"
    assert presentation.humanize_reason_text(reason) == "The AI service couldn't be reached."


def test_real_client_5xx_produces_mapped_reason():
    reason = _reason_from_gateway_status(529, {"detail": "overloaded"})
    assert reason == 'gateway returned 529: {"detail": "overloaded"}'
    assert presentation.humanize_reason_text(reason) == "The AI service couldn't be reached."


def test_real_client_408_produces_mapped_reason():
    reason = _reason_from_gateway_status(408, {"detail": "request timeout"})
    assert reason == 'gateway returned 408: {"detail": "request timeout"}'
    assert presentation.humanize_reason_text(reason) == "The AI service couldn't be reached."


def test_real_client_4xx_produces_read_failure_reason():
    reason = _reason_from_gateway_status(413, {"detail": "request too large"})
    assert reason == 'gateway returned 413: {"detail": "request too large"}'
    assert presentation.humanize_reason_text(reason) == "This document couldn't be read automatically."


def test_real_client_transport_failure_produces_mapped_reason():
    reason = _reason_from_transport_failure()
    assert reason.startswith("gateway unreachable at http://gw.test:8010/run:")
    assert presentation.humanize_reason_text(reason) == "The AI service couldn't be reached."


def test_gateway_error_with_pdf_detail_is_a_file_problem():
    reason = _reason_from_gateway_status(400, {"detail": "The PDF specified was not valid."})
    assert reason == "gateway returned 400: {\"detail\": 'The PDF specified was not valid.'}" or reason == 'gateway returned 400: {"detail": "The PDF specified was not valid."}'
    assert presentation.humanize_reason_text(reason) == "The file couldn't be read."