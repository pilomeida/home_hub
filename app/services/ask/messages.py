"""What Ask tells a person when something did not work, in the three-part form of the messages rule
(decision D14): what didn't happen, why, what they can do. The engine stores a failure as JSON in
AskTurn.error (the raw technical text kept apart, for whoever has to dig); the page reads it back here."""

from __future__ import annotations

import json
from typing import Optional

from app.models.ask import AskTurn
from app.privacy.reach import LeftOut
from app.problems import Problem, classify_reason, compose, explain
from app.services.ask.private import private_notices

WHAT = "I couldn't answer that question."


def refused() -> Problem:
    return Problem(WHAT, "The AI declined to answer it.",
                   "Try putting the question differently. If you think it should be allowed, tell Pedro.")


def empty_reply() -> Problem:
    return Problem(WHAT, "The AI came back without any text.", "Ask again. If it keeps happening, tell Pedro.")


def out_of_steps(rounds: int) -> Problem:
    return Problem("I couldn't finish answering that question.",
                   f"It needed more than the {rounds} look-ups one question is allowed, without finding an answer.",
                   "Ask something narrower, for example about one document or one month at a time.")


def from_exception(exc: Exception) -> tuple[Problem, str]:
    """(message, technical text) for a failure raised while answering. Gateway and privacy refusals are
    explained by app.problems.classify_reason; anything else is an unexpected error inside the Hub."""
    raw = str(getattr(exc, "reason", None) or exc)
    cause = classify_reason(raw)
    if cause is not None:
        problem = compose(WHAT, cause)
    else:                                                          # nothing recognisable: say so plainly
        problem = Problem(WHAT, "Something unexpected went wrong inside the Hub.",
                          "Try again. If it repeats, tell Pedro; the Problems page keeps the technical details.",
                          link="/problems")
    return problem, f"{type(exc).__name__}: {raw}"


def encode(problem: Problem, technical: Optional[str] = None) -> str:
    return json.dumps({"what": problem.what, "why": problem.why, "next_step": problem.next_step,
                       "link": problem.link, "technical": technical})


def failure(turn: AskTurn) -> Optional[tuple[Problem, Optional[str]]]:
    """The failure of a turn as a message. Rows written before the messages rule hold plain text."""
    if not turn.error:
        return None
    try:
        data = json.loads(turn.error)
        if isinstance(data, dict) and {"what", "why", "next_step"} <= set(data):
            return Problem(data["what"], data["why"], data["next_step"], data.get("link")), data.get("technical")
    except ValueError:
        pass
    return explain(WHAT, turn.error), turn.error


def notices(turn: AskTurn) -> list[Problem]:
    """Notes under an answer: it turned private, documents that weren't available, earlier answers that weren't reused."""
    left = LeftOut.from_json(turn.left_out_json)
    return [*private_notices(turn),
            *[p for p in (left.problem("answer"), left.history_problem()) if p is not None]]
