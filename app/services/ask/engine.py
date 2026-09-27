"""Ask engine: answers one AskTurn with a bounded tool-use loop over the
wiki (first) and raw sources (fallback), with the conversation's bounded
history in front. Read-only except for the turn row itself.

Runs through the LLM gateway (llmsel): one `job_id` (uuid4) per question,
re-sent every turn; the same tools in the same order each turn;
`assistant_content` is appended verbatim; usage accumulates from
`result.usage`. Adaptive thinking is the gateway's business now.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict
from datetime import date, datetime
from typing import Optional

from sqlmodel import Session

from app.models.ask import AskConversation, AskStatus, AskTurn
from app.services.ask.citations import cited_refs
from app.services.ask.contracts import AskTool, Citable, ToolOutput
from app.services.ask.context import build_system_prompt
from app.services.ask.conversation import build_history
from app.services.ask.tools import READ_FILE_TOOL, available_tools
from app.llm_gateway import GatewayError, INTERACTIVE_RESEARCH, get_gateway

logger = logging.getLogger(__name__)

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


async def run_turn(session: Session, turn_id: int, gateway=None,
                   today: Optional[date] = None) -> AskTurn:
    turn = session.get(AskTurn, turn_id)
    gw = gateway or get_gateway()
    system, index_citables = build_system_prompt(session, today or date.today())
    history, history_citables = build_history(session, turn)
    tools = available_tools()
    by_name = {t.name: t for t in tools}
    known: dict[str, Citable] = {c.ref: c for c in index_citables}
    known.update(history_citables)
    messages: list[dict] = [*history, {"role": "user", "content": turn.question}]
    tool_specs = [t.to_api() for t in tools]  # same tools, same order, every turn
    job_id = str(uuid.uuid4())
    file_reads, used_raw, in_tokens, out_tokens = 0, False, 0, 0
    answer: Optional[str] = None
    error: Optional[str] = None

    try:
        for _ in range(_MAX_TURNS):
            result = await gw.run(
                INTERACTIVE_RESEARCH, system,
                messages=messages, tools=tool_specs, job_id=job_id,
                max_tokens=_MAX_TOKENS,
            )
            in_tokens += result.usage.get("input_tokens", 0)
            out_tokens += result.usage.get("output_tokens", 0)
            if result.stop_reason == "refusal":
                error = "The AI declined to answer this question."
                break
            if result.stop_reason != "tool_use":
                answer = "".join(
                    block.get("text", "") for block in result.assistant_content
                    if isinstance(block, dict) and block.get("type") == "text"
                ).strip() or result.text.strip() or None
                error = None if answer else "No answer was produced."
                break
            messages.append({"role": "assistant", "content": result.assistant_content})
            results = []
            for call in result.tool_calls:
                name, block_id, call_input = call["name"], call["id"], call.get("input") or {}
                if name == READ_FILE_TOOL and file_reads >= _MAX_FILE_READS:
                    output = ToolOutput(text="File-read limit reached for this question.", is_error=True)
                else:
                    output = _run_tool(session, by_name.get(name), name, call_input)
                    if name == READ_FILE_TOOL and not output.is_error:
                        file_reads += 1
                known.update({c.ref: c for c in output.citables})
                used_raw = used_raw or output.used_raw_sources
                results.append(_tool_result(block_id, output))
            messages.append({"role": "user", "content": results})
        else:
            error = "Ran out of research steps before finding an answer."
    except GatewayError:
        logger.exception("Ask: gateway call failed")
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
