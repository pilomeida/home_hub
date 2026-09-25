"""Ask engine: answers one AskTurn with a bounded Claude tool-use loop over
the wiki (first) and raw sources (fallback), with the conversation's
bounded history in front. Read-only except for the turn row itself."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date, datetime
from typing import Optional

import anthropic
from anthropic import AsyncAnthropic
from sqlmodel import Session

from app.config import settings
from app.models.ask import AskConversation, AskStatus, AskTurn
from app.services.ask.citations import cited_refs
from app.services.ask.contracts import AskTool, Citable, ToolOutput
from app.services.ask.context import build_system_prompt
from app.services.ask.conversation import build_history
from app.services.ask.tools import READ_FILE_TOOL, available_tools

logger = logging.getLogger(__name__)

_MODEL = "claude-opus-5-5"   # thinking can't be disabled; default effort (medium); tool_choice stays auto
_MAX_TOKENS = 16000
_MAX_TURNS = 8               # model round-trips per question
_MAX_FILE_READS = 2


def _run_tool(session: Session, tool: Optional[AskTool], name: str, args: dict) -> ToolOutput:
    if tool is None:
        return ToolOutput(text=f"Unknown tool {name!r}.", is_error=True)
    try:
        return tool.run(session, args or {})
    except Exception as exc:  # a tool bug must not kill the answer
        logger.exception("Ask tool %s failed", name)
        session.rollback()
        return ToolOutput(text=f"Tool failed: {exc}", is_error=True)


def _tool_result(block_id: str, output: ToolOutput) -> dict:
    content = [{"type": "text", "text": output.text}, *output.content_blocks] if output.content_blocks else output.text
    return {"type": "tool_result", "tool_use_id": block_id, "content": content, "is_error": output.is_error}


async def run_turn(session: Session, turn_id: int, client: Optional[AsyncAnthropic] = None,
                   today: Optional[date] = None) -> AskTurn:
    turn = session.get(AskTurn, turn_id)
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    system, index_citables = build_system_prompt(session, today or date.today())
    history, history_citables = build_history(session, turn)
    tools = available_tools()
    by_name = {t.name: t for t in tools}
    known: dict[str, Citable] = {c.ref: c for c in index_citables}
    known.update(history_citables)
    messages: list[dict] = [*history, {"role": "user", "content": turn.question}]
    file_reads, used_raw, in_tokens, out_tokens = 0, False, 0, 0
    answer: Optional[str] = None
    error: Optional[str] = None

    try:
        for _ in range(_MAX_TURNS):
            response = await anthropic_client.messages.create(
                model=_MODEL, max_tokens=_MAX_TOKENS, thinking={"type": "adaptive"},
                system=system, tools=[t.to_api() for t in tools], messages=messages,
            )
            in_tokens += response.usage.input_tokens
            out_tokens += response.usage.output_tokens
            if response.stop_reason == "refusal":
                error = "The AI declined to answer this question."
                break
            if response.stop_reason != "tool_use":
                answer = "".join(b.text for b in response.content if b.type == "text").strip() or None
                error = None if answer else "No answer was produced."
                break
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name == READ_FILE_TOOL and file_reads >= _MAX_FILE_READS:
                    output = ToolOutput(text="File-read limit reached for this question.", is_error=True)
                else:
                    output = _run_tool(session, by_name.get(block.name), block.name, block.input)
                    if block.name == READ_FILE_TOOL and not output.is_error:
                        file_reads += 1
                known.update({c.ref: c for c in output.citables})
                used_raw = used_raw or output.used_raw_sources
                results.append(_tool_result(block.id, output))
            messages.append({"role": "user", "content": results})
        else:
            error = "Ran out of research steps before finding an answer."
    except anthropic.APIError:
        logger.exception("Ask: Claude API call failed")
        error = "The AI service is unavailable right now."

    turn = session.get(AskTurn, turn_id)
    turn.input_tokens, turn.output_tokens, turn.used_raw_sources = in_tokens, out_tokens, used_raw
    if answer:
        turn.status, turn.answer_text = AskStatus.ANSWERED, answer
        turn.citations_json = json.dumps([asdict(known[r]) for r in cited_refs(answer, known)])
    else:
        turn.status, turn.error = AskStatus.FAILED, error
    turn.answered_at = datetime.utcnow()
    conv = session.get(AskConversation, turn.conversation_id)
    conv.updated_at = turn.answered_at
    session.add(turn)
    session.add(conv)
    session.commit()
    session.refresh(turn)
    return turn
