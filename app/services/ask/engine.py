"""Ask engine: answers one AskTurn with a bounded tool-use loop over the
wiki (first) and raw sources (fallback), with the conversation's bounded
history in front. Read-only except for the turn row itself.

Runs through the LLM gateway (llmsel): one `job_id` (uuid4) per question,
re-sent every turn; the same tools in the same order each turn;
`assistant_content` is appended verbatim; usage accumulates from
`result.usage`. Adaptive thinking is the gateway's business now.

Privacy (decision D16): the whole run happens inside `reachable_scope`, so while levels are binding
every gateway call carries the strictest level among the types Ask may read, and everything the
engine shows the model (prompt, history, tools) is limited to those types. The turn records what was
left out so the page can say so."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict
from datetime import date, datetime
from typing import Optional

from sqlmodel import Session

from app.models.ask import AskConversation, AskStatus, AskTurn
from app.privacy.ratchet import Ratchet, highest, ratchet_scope
from app.privacy.reach import LeftOut, Reach
from app.privacy.scope import reachable_scope
from app.privacy.tiers import Tier
from app.problems import Problem, record_problem
from app.services.ask import messages as ask_messages
from app.services.ask.citations import cited_refs
from app.services.ask.contracts import AskTool, Citable, ToolOutput
from app.services.ask.context import build_system_prompt
from app.services.ask.conversation import build_history, privacy_dropped
from app.services.ask.private import chat_is_private
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
        # A fixed text: the exception message may hold record content, and this result goes out untiered.
        return ToolOutput(text="That look-up failed, so nothing was returned. Answer without it.", is_error=True)


def _tool_result(block_id: str, output: ToolOutput) -> dict:
    content = [{"type": "text", "text": output.text}, *output.content_blocks] if output.content_blocks else output.text
    return {"type": "tool_result", "tool_use_id": block_id, "content": content, "is_error": output.is_error}


async def run_turn(session: Session, turn_id: int, gateway=None,
                   today: Optional[date] = None) -> AskTurn:
    """Answer one turn. The scopes are entered HERE, inside the code that actually runs (the router runs
    this in a background task with its own session): the privacy scope gives the first call its level, and
    the ratchet makes sure that once a tool returns private content nothing is sent below High (D18)."""
    with reachable_scope(session) as reach:
        turn = session.get(AskTurn, turn_id)
        start = Tier.HIGH if chat_is_private(session, turn) else reach.tier
        with ratchet_scope(start) as ratchet:
            return await _answer(session, turn_id, gateway, today, reach, ratchet)


async def _answer(session: Session, turn_id: int, gateway, today: Optional[date], reach: Reach,
                  ratchet: Ratchet) -> AskTurn:
    turn = session.get(AskTurn, turn_id)
    left_out = LeftOut(reach.binding, tuple(sorted(reach.excluded_keys)))
    known: dict[str, Citable] = {}
    job_id = str(uuid.uuid4())
    file_reads, used_raw, in_tokens, out_tokens = 0, False, 0, 0
    answer: Optional[str] = None
    failure: Optional[tuple[Problem, Optional[str]]] = None

    try:
        gw = gateway or get_gateway()
        left_out = reach.left_out(session, history_dropped=privacy_dropped(session, turn))
        system, index_citables = build_system_prompt(session, today or date.today(), left_out,
                                                     reach=reach.without_private())
        history, history_citables = build_history(session, turn)
        tools = available_tools()
        by_name = {t.name: t for t in tools}
        known = {c.ref: c for c in index_citables}
        known.update(history_citables)
        messages: list[dict] = [*history, {"role": "user", "content": turn.question}]
        tool_specs = [t.to_api() for t in tools]  # same tools, same order, every turn
        for round_no in range(1, _MAX_TURNS + 1):
            result = await gw.run(
                INTERACTIVE_RESEARCH, system,
                messages=messages, tools=tool_specs, job_id=job_id,
                max_tokens=_MAX_TOKENS,
            )
            in_tokens += result.usage.get("input_tokens", 0)
            out_tokens += result.usage.get("output_tokens", 0)
            if result.stop_reason == "refusal":
                failure = (ask_messages.refused(), None)
                break
            if result.stop_reason != "tool_use":
                answer = "".join(
                    block.get("text", "") for block in result.assistant_content
                    if isinstance(block, dict) and block.get("type") == "text"
                ).strip() or result.text.strip() or None
                failure = None if answer else (ask_messages.empty_reply(), None)
                break
            messages.append({"role": "assistant", "content": result.assistant_content})
            results = []
            raised: list[ToolOutput] = []
            for call in result.tool_calls:
                name, block_id, call_input = call["name"], call["id"], call.get("input") or {}
                if name == READ_FILE_TOOL and file_reads >= _MAX_FILE_READS:
                    output = ToolOutput(text="File-read limit reached for this question.", is_error=True)
                else:
                    output = _run_tool(session, by_name.get(name), name, call_input)
                    if name == READ_FILE_TOOL and not output.is_error:
                        file_reads += 1
                if output.tier is not None:
                    raised.append(output)
                known.update({c.ref: c for c in output.citables})
                used_raw = used_raw or output.used_raw_sources
                results.append(_tool_result(block_id, output))
            if raised:        # one escalation per round, naming everything this round opened
                ratchet.raise_to(highest(o.tier for o in raised), step=round_no,
                                 label=" and ".join(o.opened or "private records" for o in raised))
            messages.append({"role": "user", "content": results})
        else:
            failure = (ask_messages.out_of_steps(_MAX_TURNS), None)
    except GatewayError as exc:
        logger.exception("Ask: gateway call failed")
        failure = ask_messages.from_exception(exc)
    except Exception as exc:  # never leave the person looking at "Looking through the wiki…" forever
        logger.exception("Ask: turn %s failed", turn_id)
        session.rollback()
        failure = ask_messages.from_exception(exc)
        record_problem(session, area="Ask", problem=failure[0], ref=f"ask:{turn_id}", technical=failure[1])

    turn = session.get(AskTurn, turn_id)
    turn.input_tokens, turn.output_tokens, turn.used_raw_sources = in_tokens, out_tokens, used_raw
    turn.left_out_json = left_out.to_json() if reach.binding else None
    turn.escalation_json = ratchet.to_json() if (ratchet.escalations or ratchet.started_private) else None
    if answer:
        turn.status, turn.answer_text = AskStatus.ANSWERED, answer
        turn.citations_json = json.dumps([asdict(known[r]) for r in cited_refs(answer, known)])
    else:
        turn.status = AskStatus.FAILED
        turn.error = ask_messages.encode(*failure)
    turn.answered_at = datetime.utcnow()
    conv = session.get(AskConversation, turn.conversation_id)
    conv.updated_at = turn.answered_at
    session.add(turn)
    session.add(conv)
    session.commit()
    session.refresh(turn)
    return turn
