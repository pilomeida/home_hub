"""The Ask tool-use loop through the gateway: one job_id per question,
same tools every turn, assistant_content appended verbatim, usage summed,
GatewayError handled."""

import json
from datetime import date

import pytest

from app.llm_gateway import GatewayError
from app.models.ask import AskStatus
from app.services.ask.conversation import add_turn, start_conversation
from app.services.ask.engine import run_turn
from tests.fakes.fake_gateway import FakeGateway
from tests.knowledge_factories import make_claim, make_document, make_page


def _usage(in_t=100, out_t=20):
    return {"input_tokens": in_t, "output_tokens": out_t}


def _text(text):
    return {"text": text, "stop_reason": "end_turn", "usage": _usage()}


def _tool(tool_calls):
    return {
        "text": "", "stop_reason": "tool_use", "usage": _usage(),
        "tool_calls": tool_calls,
        "assistant_content": [
            {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c.get("input", {})}
            for c in tool_calls
        ],
    }


def _refusal():
    return {"text": "", "stop_reason": "refusal", "usage": _usage()}


@pytest.mark.asyncio
async def test_wiki_first_answer_with_valid_citations(session, fake_domain):
    doc = make_document(session, filename="service.pdf")
    boiler = make_page(session, "Boiler", summary="Vaillant boiler")
    make_claim(session, boiler, "last_service", "2026-03-02", doc_ids=[doc.id])
    gateway = FakeGateway([
        _tool([{"id": "tu_1", "name": "read_wiki_pages", "input": {"page_ids": [boiler.id]}}]),
        _text(f"Last serviced on 2 March 2026 [[wiki:{boiler.id}]][[doc:{doc.id}]][[doc:999]]."),
    ])
    turn = start_conversation(session, "When was the boiler last serviced?", started_by="pedro@example.com")

    result = await run_turn(session, turn.id, gateway=gateway, today=date(2026, 9, 24))

    assert result.status == AskStatus.ANSWERED and result.used_raw_sources is False
    assert [c["ref"] for c in json.loads(result.citations_json)] == [f"wiki:{boiler.id}", f"doc:{doc.id}"]
    first = gateway.requests[0]
    assert first["workload_type"] == "interactive_research"
    assert first["job_id"] and isinstance(first["job_id"], str) and len(first["job_id"]) == 36
    assert "model" not in first and "thinking" not in first and first["max_tokens"] == 16000
    assert "tool_choice" not in first and f"wiki:{boiler.id} Boiler" in first["system"]
    assert first["messages"] == [{"role": "user", "content": "When was the boiler last serviced?"}]
    second = gateway.requests[1]
    assert second["job_id"] == first["job_id"]  # same job, re-sent every turn
    msgs = second["messages"]
    assert msgs[1]["role"] == "assistant" and msgs[1]["content"][0]["type"] == "tool_use"
    assert msgs[-1]["role"] == "user" and msgs[-1]["content"][0]["type"] == "tool_result"
    assert msgs[-1]["content"][0]["tool_use_id"] == "tu_1"
    assert result.input_tokens == 200 and result.output_tokens == 40


@pytest.mark.asyncio
async def test_follow_up_gets_history_and_may_recite_earlier_refs(session, fake_domain):
    boiler = make_page(session, "Boiler")
    turn1 = start_conversation(session, "When was the boiler serviced?", started_by=None)
    await run_turn(session, turn1.id, gateway=FakeGateway([_text(f"March 2026 [[wiki:{boiler.id}]].")]))
    turn2 = add_turn(session, turn1.conversation_id, "and who serviced it?", asked_by=None)
    gateway = FakeGateway([_text(f"The same visit, per the boiler page [[wiki:{boiler.id}]].")])

    result = await run_turn(session, turn2.id, gateway=gateway)

    sent = gateway.requests[0]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[0]["content"] == "When was the boiler serviced?" and "Sources cited" in sent[1]["content"]
    assert sent[2]["content"] == "and who serviced it?"
    assert [c["ref"] for c in json.loads(result.citations_json)] == [f"wiki:{boiler.id}"]


@pytest.mark.asyncio
async def test_raw_source_fallback_sets_flag(session, fake_domain):
    doc = make_document(session, filename="dishwasher-manual.pdf")
    turn = start_conversation(session, "Do we have the dishwasher manual?", started_by=None)
    gateway = FakeGateway([
        _tool([{"id": "tu_1", "name": "find_sources", "input": {"query": "dishwasher"}}]),
        _text(f"Yes [[doc:{doc.id}]]."),
    ])
    assert (await run_turn(session, turn.id, gateway=gateway)).used_raw_sources is True


@pytest.mark.asyncio
async def test_file_reads_are_capped_at_two(session, fake_domain, tmp_path):
    pdf = tmp_path / "m.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    doc = make_document(session, filename="m.pdf", file_path=str(pdf))
    turn = start_conversation(session, "q", started_by=None)
    gateway = FakeGateway(
        [_tool([{"id": f"tu_{i}", "name": "read_document_file", "input": {"document_id": doc.id}}]) for i in range(1, 4)]
        + [_text("Done.")])
    await run_turn(session, turn.id, gateway=gateway)
    results = [r
               for req in gateway.requests[1:4]
               for r in req["messages"][-1]["content"]]
    assert [r["is_error"] for r in results] == [False, False, True]
    # the file block rides in the tool_result content list (brief 07 §2, door 2)
    assert results[0]["content"][1]["type"] == "document"


@pytest.mark.asyncio
async def test_unknown_tool_and_tool_crash_become_error_results(session, fake_domain, monkeypatch):
    def _boom(session, args):
        raise RuntimeError("db exploded")
    monkeypatch.setattr("app.services.ask.tools._list_todos", _boom)
    turn = start_conversation(session, "q", started_by=None)
    gateway = FakeGateway([
        _tool([{"id": "tu_1", "name": "no_such_tool", "input": {}},
               {"id": "tu_2", "name": "list_todos", "input": {}}]),
        _text("Not found."),
    ])
    result = await run_turn(session, turn.id, gateway=gateway)
    assert all(r["is_error"] for r in gateway.requests[1]["messages"][-1]["content"])
    assert result.status == AskStatus.ANSWERED


@pytest.mark.asyncio
async def test_runaway_loop_fails_gracefully(session, fake_domain):
    turn = start_conversation(session, "q", started_by=None)
    gateway = FakeGateway([_tool([{"id": "tu_1", "name": "list_todos", "input": {}}])] * 8)
    result = await run_turn(session, turn.id, gateway=gateway)
    assert result.status == AskStatus.FAILED and "research steps" in result.error


@pytest.mark.asyncio
async def test_refusal_marks_failed(session, fake_domain):
    turn = start_conversation(session, "q", started_by=None)
    result = await run_turn(session, turn.id, gateway=FakeGateway([_refusal()]))
    assert result.status == AskStatus.FAILED and "declined" in result.error


@pytest.mark.asyncio
async def test_gateway_error_marks_failed(session, fake_domain):
    turn = start_conversation(session, "q", started_by=None)
    gateway = FakeGateway(errors=[GatewayError(status=503, detail="overloaded")])
    result = await run_turn(session, turn.id, gateway=gateway)
    assert result.status == AskStatus.FAILED and "unavailable" in result.error
