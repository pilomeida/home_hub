"""One check-result shape (PLAN step 5c): what every check of a reading returns, so they all read the same way and
all reach "Waiting for you" the same way.

A `CheckResult` has a verdict, `ok` (the reading agrees with the paper), `doubtful` (something does not agree: the
doubts say what, in plain words) or `not_checked` (nothing independent to check against: a scan, a statement
without balances), and the doubtful items. Adapters turn each check's own detail into it: the statement add-up
(`statement_check.Verdict`), loan and fund positions (their list of problems), the card statement (refused unless it
adds up, so a stored one is `ok`) and the Health check against the paper (`health.checks.CheckReport`).
`read_twice` is the shared "read it again, independently, and compare" step (Health uses it on every paper)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Sequence

OK, DOUBTFUL, NOT_CHECKED = "ok", "doubtful", "not_checked"


@dataclass(frozen=True)
class Doubt:
    message: str                     # plain words, for the person
    ref: Optional[str] = None        # what it is about (a line number, a test), when there is one


@dataclass
class CheckResult:
    verdict: str
    doubts: list[Doubt] = field(default_factory=list)
    summary: str = ""

    @property
    def clean(self) -> bool:
        return self.verdict == OK

    def notes(self, limit: Optional[int] = None) -> list[str]:
        """The doubts as plain lines; past `limit`, one line saying how many more."""
        lines = [d.message for d in self.doubts]
        if limit is not None and len(lines) > limit:
            lines = lines[:limit] + [f"... and {len(lines) - limit} more. Look at the paper."]
        return lines


def from_doubts(doubts: Sequence[Doubt], *, checked: bool = True, summary: str = "") -> CheckResult:
    if not checked:
        return CheckResult(NOT_CHECKED, list(doubts), summary)
    return CheckResult(DOUBTFUL if doubts else OK, list(doubts), summary)


def from_statement(verdict) -> CheckResult:
    """The statement add-up (app/services/statement_check.py): `ok`, `mismatch` (doubtful) or `unverified`."""
    from app.services.statement_check import MISMATCH, OK as ADDS_UP
    doubts = [Doubt(reason, ref=f"line {index + 1}") for index, reason in sorted(verdict.flagged.items())]
    if verdict.status == MISMATCH and not doubts:
        doubts = [Doubt(verdict.note)]
    status = OK if verdict.status == ADDS_UP else DOUBTFUL if verdict.status == MISMATCH else NOT_CHECKED
    return CheckResult(status, doubts, verdict.note)


def from_problems(problems: Sequence[str], *, summary: str = "") -> CheckResult:
    """Loan and fund positions, and any check that lists its problems in words."""
    return from_doubts([Doubt(p) for p in problems], summary=summary)


def card_statement_checked(lines: int) -> CheckResult:
    """A card statement is refused unless its lines add up to its own balances to the cent (card_figures)."""
    return CheckResult(OK, [], f"{lines} line(s), checked to the cent against the statement's own balances")


def from_health(report, *, had_text: bool) -> CheckResult:
    """The Health reading checked against the paper's own text (app/categories/health/checks.py)."""
    from app.categories.health.checks import unresolved_notes
    doubts = [Doubt(n) for n in unresolved_notes(report)]
    return from_doubts(doubts, checked=had_text)


async def read_twice(second_read: Callable[[], Awaitable], compare: Callable[[object], Sequence[str]]) -> list[Doubt]:
    """Read the paper again, independently, and say where the two readings disagree. When the second reading fails,
    the first stands and the doubt says it was not cross-checked."""
    try:
        other = await second_read()
    except Exception as exc:
        return [Doubt(f"A second reading to compare against did not work ({type(exc).__name__}); this reading is "
                      "unchecked. Read the paper again to check it.")]
    return [Doubt(m) for m in compare(other)]


__all__ = ["OK", "DOUBTFUL", "NOT_CHECKED", "Doubt", "CheckResult", "from_doubts", "from_statement", "from_problems",
           "card_statement_checked", "from_health", "read_twice"]
