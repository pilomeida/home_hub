import json
from datetime import date

import anthropic
import httpx
import pytest

from app.models.ask import AskStatus
from app.services.ask.conversation import add_turn, start_conversation
from app.services.ask.engine import run_turn
from tests.fake_anthropic import FakeAnthropic, refusal_response, text_response, tool_response
from tests.knowledge_factories import make_claim, make_document, make_page


@pytest.mark.asyncio
async def test_wiki_first_answer_with_valid_citations(session, fake_domain):
    doc = make_document(session, filename="service.pdf")
    boiler = make_page(session, "Boiler", summary="Vaillant boiler")
    make_claim(session, boiler, "last_service", "2026-03-02", doc_ids=[doc.id])
    client = FakeAnthropic([
        tool_response(("read_wiki_pages", {"page_ids": [boiler.id]})),
        text_response(f"Last serviced on 2 March 2026 [[wiki:{boiler.id}]][[doc:{doc.id}]][[doc:999]]."),
    ])
    turn = start_conversation(session, "When was the boiler last serviced?", started_by="pedro@example.com")

    result = await run_turn(session, turn.id, client=client, today=date(2026, 9, 24))

    assert result.status == AskStatus.ANSWERED and result.used_raw_sources is False
    assert [c["ref"] for c in json.loads(result.citations_json)] == [f"wiki:{boiler.id}", f"doc:{doc.id}"]
    first = client.messages.calls[0]
    assert first["model"] == "claude-opus-5-5" and first["thinking"] == {"type": "adaptive"}
    assert "tool_choice" not in first and f"wiki:{boiler.id} Boiler" in first["system"]
    assert first["messages"] == [{"role": "user", "content": "When was the boiler last serviced?"}]
    second = client.messages.calls[1]["messages"]
    assert second[1]["role"] == "assistant" and second[-1]["content"][0]["type"] == "tool_result"
    assert result.input_tokens == 200 and result.output_tokens == 40


@pytest.mark.asyncio
async def test_follow_up_gets_history_and_may_recite_earlier_refs(session, fake_domain):
    boiler = make_page(session, "Boiler")
    turn1 = start_conversation(session, "When was the boiler serviced?", started_by=None)
    await run_turn(session, turn1.id, client=FakeAnthropic([text_response(f"March 2026 [[wiki:{boiler.id}]].")]))
    turn2 = add_turn(session, turn1.conversation_id, "and who serviced it?", asked_by=None)
    client = FakeAnthropic([text_response(f"The same visit, per the boiler page [[wiki:{boiler.id}]].")])

    result = await run_turn(session, turn2.id, client=client)

    sent = client.messages.calls[0]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[0]["content"] == "When was the boiler serviced?" and "Sources cited" in sent[1]["content"]
    assert sent[2]["content"] == "and who serviced it?"
    assert [c["ref"] for c in json.loads(result.citations_json)] == [f"wiki:{boiler.id}"]


@pytest.mark.asyncio
async def test_raw_source_fallback_sets_flag(session, fake_domain):
    doc = make_document(session, filename="dishwasher-manual.pdf")
    turn = start_conversation(session, "Do we have the dishwasher manual?", started_by=None)
    client = FakeAnthropic([tool_response(("find_sources", {"query": "dishwasher"})),
                            text_response(f"Yes [[doc:{doc.id}]].")])
    assert (await run_turn(session, turn.id, client=client)).used_raw_sources is True


@pytest.mark.asyncio
async def test_file_reads_are_capped_at_two(session, fake_domain, tmp_path):
    pdf = tmp_path / "m.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    doc = make_document(session, filename="m.pdf", file_path=str(pdf))
    turn = start_conversation(session, "q", started_by=None)
    client = FakeAnthropic([tool_response(*[("read_document_file", {"document_id": doc.id})] * 3),
                            text_response("Done.")])
    await run_turn(session, turn.id, client=client)
    assert [r["is_error"] for r in client.messages.calls[1]["messages"][-1]["content"]] == [False, False, True]


@pytest.mark.asyncio
async def test_unknown_tool_and_tool_crash_become_error_results(session, fake_domain, monkeypatch):
    def _boom(session, args):
        raise RuntimeError("db exploded")
    monkeypatch.setattr("app.services.ask.tools._list_todos", _boom)
    turn = start_conversation(session, "q", started_by=None)
    client = FakeAnthropic([tool_response(("no_such_tool", {}), ("list_todos", {})), text_response("Not found.")])
    result = await run_turn(session, turn.id, client=client)
    assert all(r["is_error"] for r in client.messages.calls[1]["messages"][-1]["content"])
    assert result.status == AskStatus.ANSWERED


@pytest.mark.asyncio
async def test_runaway_loop_fails_gracefully(session, fake_domain):
    turn = start_conversation(session, "q", started_by=None)
    result = await run_turn(session, turn.id, client=FakeAnthropic([tool_response(("list_todos", {}))] * 8))
    assert result.status == AskStatus.FAILED and "research steps" in result.error


@pytest.mark.asyncio
async def test_refusal_marks_failed(session, fake_domain):
    turn = start_conversation(session, "q", started_by=None)
    result = await run_turn(session, turn.id, client=FakeAnthropic([refusal_response()]))
    assert result.status == AskStatus.FAILED and "declined" in result.error


@pytest.mark.asyncio
async def test_api_error_marks_failed(session, fake_domain):
    class _Down:
        class messages:
            @staticmethod
            async def create(**kwargs):
                raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    turn = start_conversation(session, "q", started_by=None)
    result = await run_turn(session, turn.id, client=_Down())
    assert result.status == AskStatus.FAILED and "unavailable" in result.error
