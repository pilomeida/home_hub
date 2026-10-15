"""Does a bank statement add up? (decision D17)

`check_statement` is a PURE function: opening balance + the signed lines = closing balance, to the cent.
It returns a verdict (`ok` / `mismatch` / `unverified`) and, when it can, the few lines that are doubtful.
`apply_statement_check` writes that verdict to the database (StatementCheck row, per-line flags).

Signs. Statement lines are stored with a positive amount and a type: a credit adds, a debit subtracts. A
"transfer" is stored with no direction, so it is read from the bank's own words in the description
(`direction_from_description`, Pedro's rule) or, failing that, from the running balance printed around the
line. A transfer whose direction can be found in neither way makes the statement "unverified": the Hub
will not guess a sign and then claim the statement adds up (or does not).

What gets held (flagged "needs_check"):
  * ok          -> nothing. The figures prove the lines.
  * mismatch    -> the lines where the printed running balances stop following on (the line itself), and
                   every line the model called unclear. If the break cannot be traced to lines, NO line is
                   held and the statement itself is the one thing to check.
  * unverified  -> only lines the model called unclear (there is nothing to prove them with).
  * more than MAX_FLAGGED lines would be held -> none is held; the note says why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional, Sequence

from sqlmodel import Session, select

from app.models.transaction import StatementCheck, Transaction
from app.services.transfer_direction import direction_from_description

TOLERANCE = 0.005          # "to the cent"
MAX_FLAGGED = 25           # more doubtful lines than this are not singled out

OK = "ok"
MISMATCH = "mismatch"
UNVERIFIED = "unverified"

NEEDS_CHECK = "needs_check"
_DECIDED = ("confirmed", "rejected")


@dataclass(frozen=True)
class CheckLine:
    """One statement line as stored: positive `amount`, `kind` = debit | credit | transfer."""
    amount: float
    kind: str
    description: str = ""
    balance_after: Optional[float] = None
    unclear: bool = False


@dataclass
class Verdict:
    status: str
    opening_balance: Optional[float]
    closing_balance: Optional[float]
    lines_total: Optional[float]          # signed sum of the lines; None when a sign could not be found
    difference: Optional[float]           # closing - (opening + lines_total); None unless both balances
    note: str
    flagged: dict[int, str] = field(default_factory=dict)   # line index (as given) -> plain-language reason


def money(value: float) -> str:
    """1200.0 -> '1.200,00' (the way the statements print it)."""
    text = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-{text}" if value < 0 and round(abs(value), 2) != 0 else text


def _close(a: float, b: float) -> bool:
    return abs(a - b) < TOLERANCE


# --- signs ---------------------------------------------------------------------------------------------

def _resolve_signs(seq: Sequence[CheckLine], opening: float) -> list[Optional[int]]:
    """+1 / -1 per line, None where the direction of a transfer cannot be found. `seq` is in time order."""
    signs: list[Optional[int]] = []
    for i, line in enumerate(seq):
        kind = (line.kind or "").strip().lower()
        if kind == "credit":
            signs.append(1)
        elif kind == "debit":
            signs.append(-1)
        else:
            way = direction_from_description(line.description)
            if way is not None:
                signs.append(1 if way == "in" else -1)
                continue
            previous = opening if i == 0 else seq[i - 1].balance_after
            sign = None
            if line.balance_after is not None and previous is not None:
                delta = line.balance_after - previous
                if _close(abs(delta), line.amount) and delta != 0:
                    sign = 1 if delta > 0 else -1
            signs.append(sign)
    return signs


# --- walking the running balances ------------------------------------------------------------------------

def _broken_segments(seq: Sequence[CheckLine], signs: Sequence[int], opening: float, closing: float
                     ) -> list[tuple[int, int]]:
    """Index ranges (first, last) of lines inside which the printed balances stop following on.
    A line with a printed balance is compared with the last printed balance (the opening balance to
    start with) plus every signed line since; lines without a printed balance in between are part of the
    same range, because the break could be any of them. The closing balance ends the last range."""
    segments: list[tuple[int, int]] = []
    if not any(line.balance_after is not None for line in seq):
        return segments          # nothing printed to walk along: the whole statement is no localisation
    anchor, running, start = opening, 0.0, 0
    for i, line in enumerate(seq):
        running += signs[i] * line.amount
        if line.balance_after is not None:
            if not _close(line.balance_after, anchor + running):
                segments.append((start, i))
            anchor, running, start = line.balance_after, 0.0, i + 1
    if start <= len(seq) - 1 and not _close(closing, anchor + running):
        segments.append((start, len(seq) - 1))
    return segments


# --- the check -----------------------------------------------------------------------------------------

def _which_missing(opening, closing) -> str:
    if opening is None and closing is None:
        return "no opening or closing balance"
    return "no opening balance" if opening is None else "no closing balance"


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def unclear_reason(line: CheckLine) -> str:
    return (f"Held out of the totals. The Hub isn't sure it read this line correctly: it is smudged, cut off "
            f"or ambiguous on the page (read as {money(line.amount)}). Check it against the original, then "
            f"confirm it, fix it or mark it as not a real line.")


def check_statement(lines: Sequence[CheckLine], opening: Optional[float], closing: Optional[float]) -> Verdict:
    lines = list(lines)
    unclear = {i: unclear_reason(line) for i, line in enumerate(lines) if line.unclear}

    def finish(status: str, total, difference, note: str, flagged: dict[int, str],
               held: str = "The {n} unclear {lines} held back in the To-check list.",
               untraced: str = "") -> Verdict:
        """Apply the cap and add the closing sentence: `held` when lines are held, `untraced` when none."""
        flagged = dict(sorted(flagged.items()))
        n = len(flagged)
        if n > MAX_FLAGGED:
            note += (f" {n} lines are doubtful, which is too many to single out one by one, so none is held "
                     f"back; check the whole statement against the original.")
            flagged = {}
        elif n:
            note += " " + held.format(n=n, lines=_plural(n, "line is", "lines are"),
                                      line=_plural(n, "line", "lines"), is_=_plural(n, "is", "are"))
        elif untraced:
            note += " " + untraced
        return Verdict(status, opening, closing, total, difference, note, flagged)

    if opening is None or closing is None:
        total = _plain_total(lines, opening)
        return finish(
            UNVERIFIED, total, None,
            f"Not checked. This statement has {_which_missing(opening, closing)} (or the Hub could not read it), "
            f"so the lines cannot be added up against the balances. The lines count as usual; compare with the "
            f"original if a figure looks wrong.", unclear)

    # Orientation: the lines are normally in time order, but some banks print newest first. Try both and
    # keep whichever explains the statement (ok first, then the fewest doubtful lines).
    candidates = []
    for reverse in (False, True):
        seq = lines[::-1] if reverse else lines
        signs = _resolve_signs(seq, opening)
        if any(s is None for s in signs):
            continue
        total = sum(s * line.amount for s, line in zip(signs, seq))
        candidates.append((reverse, seq, signs, total))
    if not candidates:
        count = sum(1 for line in lines if (line.kind or "").strip().lower() == "transfer")
        return finish(
            UNVERIFIED, None, None,
            f"Not checked. {count} transfer {_plural(count, 'line does', 'lines do')} not say whether money went "
            f"in or out, so the Hub cannot add the statement up. The lines count as usual; compare with the "
            f"original if a figure looks wrong.", unclear)

    for _, _, _, total in candidates:
        if _close(opening + total, closing):
            return Verdict(OK, opening, closing, total, closing - (opening + total),
                           f"Adds up: the opening balance {money(opening)} plus the lines gives the closing "
                           f"balance {money(closing)}.", {})

    scored = []
    for reverse, seq, signs, total in candidates:
        segments = _broken_segments(seq, signs, opening, closing)
        scored.append((sum(last - first + 1 for first, last in segments), reverse, seq, total, segments))
    scored.sort(key=lambda row: (row[0], row[1]))          # fewest doubtful lines; time order on a tie
    _, reverse, seq, total, segments = scored[0]
    difference = closing - (opening + total)
    n = len(lines)

    def original_index(i: int) -> int:
        return n - 1 - i if reverse else i

    flagged = dict(unclear)
    traced = False
    for first, last in segments:
        span = last - first + 1
        for i in range(first, last + 1):
            idx = original_index(i)
            if span > 1:
                reason = (f"Held out of the totals. The balances printed on the statement stop adding up somewhere "
                          f"within these {span} lines, so this one may have been misread. Check them against the "
                          f"original, then confirm, fix or mark each as not a real line.")
            elif seq[i].balance_after is not None:
                reason = (f"Held out of the totals. The balance printed after this line "
                          f"({money(seq[i].balance_after)}) does not follow from the line before it, so this line "
                          f"may have been misread, or a line just before it missed. Check it against the original, "
                          f"then confirm it, fix it or mark it as not a real line.")
            else:
                reason = (f"Held out of the totals. The closing balance does not follow from the lines up to and "
                          f"including this one, so this line may have been misread, or a line after it missed. "
                          f"Check it against the original, then confirm it, fix it or mark it as not a real line.")
            flagged[idx] = f"{flagged[idx]} {reason}" if idx in flagged else reason
            traced = True

    direction = "less" if difference > 0 else "more"
    note = (f"Doesn't add up. The lines add up to {money(abs(difference))} {direction} than the closing balance "
            f"{money(closing)} needs (difference {money(abs(difference))}), so a line may have been missed or "
            f"misread.")
    if traced:
        held = ("The {n} doubtful {line} (where the printed balances stop following on, or that the Hub could not "
                "read clearly) {is_} held back in the To-check list.")
        untraced = ""
    else:
        untraced = ("The Hub could not tell which line is wrong. Compare the statement with the original, or accept "
                    "the difference if it is fine.")
        held = "The {n} unclear {lines} held back in the To-check list. " + untraced
    return finish(MISMATCH, total, difference, note, flagged, held=held, untraced=untraced)


def lines_from_extracted(extracted) -> list[CheckLine]:
    """The lines of an ExtractedStatement as the check reads them (amount as stored: the positive magnitude)."""
    return [CheckLine(amount=abs(item.amount), kind=item.transaction_type, description=item.description,
                      balance_after=getattr(item, "balance_after", None),
                      unclear=bool(getattr(item, "unclear", False)))
            for item in extracted.transactions]


def check_extracted(extracted) -> Verdict:
    return check_statement(lines_from_extracted(extracted), getattr(extracted, "opening_balance", None),
                           getattr(extracted, "closing_balance", None))


def _plain_total(lines: Sequence[CheckLine], opening: Optional[float]) -> Optional[float]:
    signs = _resolve_signs(lines, opening) if lines else []
    if any(s is None for s in signs):
        return None
    return sum(s * line.amount for s, line in zip(signs, lines))


# --- writing the verdict -----------------------------------------------------------------------------------

def _is_decided(row: Transaction) -> bool:
    return row.review_status in _DECIDED or bool(row.checked_by)


def apply_statement_check(session: Session, document_id: int, rows: Sequence[Transaction],
                          extracted) -> Verdict:
    """Check `extracted` (an ExtractedStatement) and store the result. `rows` are the Transaction rows made
    from `extracted.transactions`, in the same order. Does not commit; the caller commits.

    Re-processing: the document's StatementCheck is replaced (a mismatch a person already accepted stays
    accepted if it is the same difference), stale "needs_check" flags are cleared, and a line a person
    already decided (confirmed / rejected, or checked_by set) is never touched."""
    verdict = check_extracted(extracted)

    flagged_ids: set[int] = set()
    for index, (row, item) in enumerate(zip(rows, extracted.transactions)):
        if _is_decided(row):
            continue
        row.source_page = getattr(item, "page", None)
        reason = verdict.flagged.get(index)
        if reason:
            row.review_status = NEEDS_CHECK
            row.review_reason = reason
            flagged_ids.add(row.id)
        elif row.review_status == NEEDS_CHECK:
            row.review_status = None
            row.review_reason = None
        session.add(row)

    # flags an earlier run left on this document's other lines that this run no longer needs
    for stale in session.exec(select(Transaction).where(
            Transaction.document_id == document_id, Transaction.review_status == NEEDS_CHECK)).all():
        if stale.id not in flagged_ids and not _is_decided(stale):
            stale.review_status = None
            stale.review_reason = None
            session.add(stale)

    previous = session.exec(select(StatementCheck).where(StatementCheck.document_id == document_id)).first()
    accepted = None
    if previous is not None:
        if (previous.status == "accepted" and verdict.status == MISMATCH and previous.difference is not None
                and verdict.difference is not None and _close(previous.difference, verdict.difference)):
            accepted = previous
        session.delete(previous)
        session.flush()
    session.add(StatementCheck(
        document_id=document_id, opening_balance=verdict.opening_balance,
        closing_balance=verdict.closing_balance, lines_total=verdict.lines_total,
        difference=verdict.difference,
        status="accepted" if accepted else verdict.status,
        note=accepted.note if accepted else verdict.note,
        checked_at=datetime.utcnow(),
        resolved_by=accepted.resolved_by if accepted else None,
        resolved_at=accepted.resolved_at if accepted else None,
    ))
    return verdict
