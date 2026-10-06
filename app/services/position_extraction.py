"""Loan / savings position extraction from bank documents via the LLM gateway.

The model only TRANSCRIBES table lines (one `ComponentRow` per line); every
aggregation and reconciliation is deterministic code in this module
(`aggregate_components`, `reconcile_loan`, `reconcile`). No database writes.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.llm_gateway import VISION_EXTRACTION, get_gateway
from app.services.document_input import build_content_block
from app.services.json_utils import strip_json_fences

TOLERANCE = 0.02

COMPONENTS = ["capital", "interest", "insurance_life", "insurance_building"]
BALANCE_KINDS = ["deposit", "investments_total", "loans_total", "card"]


class PositionExtractionError(Exception):
    pass


@dataclass
class ComponentRow:
    date: date
    instalment_number: int
    component: str  # capital | interest | insurance_life | insurance_building
    amount: float  # positive magnitude
    balance_after: Optional[float]


@dataclass
class Instalment:
    number: int
    date: date  # date of the earliest piece
    capital: float
    interest: float
    insurance: float  # life + building
    balance_after: Optional[float]  # balance after the LAST capital piece
    insurance_life: float = 0.0
    insurance_building: float = 0.0


@dataclass
class ExtractedLoan:
    number: str
    label: str
    rate_percent: Optional[float]
    term_months: Optional[int]
    capital_granted: Optional[float]
    start_date: Optional[date]
    capital_remaining: float
    opening_balance: Optional[float]
    rows: list[ComponentRow]
    next_due_date: Optional[date]
    next_instalment_number: Optional[int]
    next_instalment: Optional[float]
    next_capital: Optional[float]
    next_interest: Optional[float]
    # TAN of the Próxima Prestação block: the REVISED rate for the next instalment.
    next_rate_percent: Optional[float] = None
    # Same block: TAN = indexante (EURIBOR) + spread. None for old payloads / when absent.
    next_indexante_percent: Optional[float] = None
    next_spread_percent: Optional[float] = None


@dataclass
class ExtractedFund:
    account_ref: str
    holder: Optional[str]
    label: str
    units: Optional[float]
    invested: Optional[float]
    value: float
    periodic_amount: Optional[float]
    next_periodic_date: Optional[date]


@dataclass
class ExtractedBalance:
    kind: str  # deposit | investments_total | loans_total | card
    label: str
    amount: float


@dataclass
class ExtractedPositions:
    as_of: date
    loans: list[ExtractedLoan]
    funds: list[ExtractedFund]
    balances: list[ExtractedBalance]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ExtractedLoanHistory:
    rows: list[ComponentRow]
    warnings: list[str] = field(default_factory=list)


# --- prompts -------------------------------------------------------------

_NUMBER_RULES = """Rules:
- Transcribe each table line as its own row. NEVER add rows together: an \
instalment paid in several pieces (several CAPITAL / CAPIT. lines with the \
same instalment number) must appear as several rows.
- The documents use Portuguese number format (1.234,56 means 1234.56). Return \
every number as a plain positive JSON number (1234.56), never as text and \
never negative; no currency symbols.
- Dates in the document are DD-MM-YYYY; return them as ISO YYYY-MM-DD.
- Map the components: CAPIT. or CAPITAL -> "capital"; JUROS -> "interest"; \
SEGURO -> "insurance_life"; SEG ED -> "insurance_building".
- "balance_after" is the value of the balance column (Saldo em Dívida / \
Empréstimo balance) shown on that very line when there is one, otherwise null.
- Loan numbers: digits only (0001.00000000001 -> "000100000000001").
- Use null wherever a value is not shown. Never guess or compute a value."""

_ROW_SHAPE = """{
        "date": "YYYY-MM-DD",
        "instalment_number": 37,
        "component": "capital | interest | insurance_life | insurance_building",
        "amount": 0.00,
        "balance_after": 0.00 or null
      }"""

_POSITIONS_SYSTEM_PROMPT = f"""You read a Portuguese bank consolidated statement \
(Extrato Consolidado, all pages) and extract ONLY the loan, savings and balance \
positions. Ignore every account movement, purchase and transfer. Respond with \
ONLY a JSON object, no prose, matching this shape:

{{
  "as_of": "YYYY-MM-DD, the statement date",
  "loans": [
    {{
      "number": "digits only",
      "label": "e.g. CRÉDITO HABITAÇÃO",
      "rate_percent": 3.125 or null,   (Tx. Juro Aplicada)
      "term_months": 300 or null,      (Prazo)
      "capital_granted": 0.00 or null, (Capital Concedido)
      "start_date": "YYYY-MM-DD or null",  (Data Formalização)
      "capital_remaining": 0.00,       (Capital Vincendo)
      "opening_balance": 0.00 or null, (Saldo Inicial of the loan block)
      "rows": [ the movement lines of the statement month, one row per line:
      {_ROW_SHAPE}
      ],
      "next_due_date": "YYYY-MM-DD or null",   (Próxima Prestação)
      "next_instalment_number": 38 or null,
      "next_instalment": 0.00 or null,  (total of the next instalment)
      "next_capital": 0.00 or null,
      "next_interest": 0.00 or null,
      "next_rate_percent": 3.5 or null,  (TAN in the Próxima Prestação block, the rate of the NEXT instalment; null if absent)
      "next_indexante_percent": 1.8 or null,  (Indexante in the Próxima Prestação block; null if absent)
      "next_spread_percent": 1.7 or null  (Spread in the Próxima Prestação block; null if absent)
    }}
  ],
  "funds": [
    {{
      "account_ref": "Conta Fundo Nº",
      "holder": "name or null",
      "label": "fund name",
      "units": 0.0 or null,            (Participação / units)
      "invested": 0.00 or null,
      "value": 0.00,                   (value at the statement date)
      "periodic_amount": 0.00 or null, (SUBSCRICAO PERIODICA, Agenda da Conta)
      "next_periodic_date": "YYYY-MM-DD or null"
    }}
  ],
  "balances": [
    {{"kind": "deposit | investments_total | loans_total | card", "label": "...", "amount": 0.00}}
  ]
}}

Where to look: Resumo das Contas (deposits: DEPÓSITOS À ORDEM -> "deposit"; \
FUNDOS DE INVESTIMENTO total -> "investments_total"); RESPONSABILIDADES \
(CRÉDITO HABITAÇÃO total -> "loans_total", CARTAO DE CREDITO -> "card"); each \
"Empréstimo" block (Capital Vincendo, PRESTAÇÃO Nº lines with CAPITAL / JUROS / \
SEGURO / SEG ED, Saldo em Dívida, Próxima Prestação); each Conta Fundo block \
and its Agenda da Conta (next periodic subscription date and amount).

{_NUMBER_RULES}"""

_POSITIONS_USER_PROMPT = "Extract the loan, fund and balance positions as JSON."

_LOAN_HISTORY_SYSTEM_PROMPT = f"""You read an online-banking printout titled \
"Consulta Movimentos Empréstimo" (movements of ONE loan) and extract ONLY the \
table rows. Each line reads: date | value date | instalment number | \
PRESTAÇÃO - CAPIT. / JUROS / SEGURO / SEG ED | amount | Saldo em Dívida. The \
balance column is filled on CAPIT. lines only. Use the first date column. \
Respond with ONLY a JSON object, no prose, matching this shape:

{{
  "rows": [
      {_ROW_SHAPE}
  ]
}}

{_NUMBER_RULES}"""

_LOAN_HISTORY_USER_PROMPT = "Extract the loan table rows as JSON."

# --- schemas -------------------------------------------------------------
# Required keys are only those the parsers index directly; everything else is
# nullable and read via .get (see tests/test_schema_strictness.py).

_NUM = {"type": ["number", "string", "null"]}  # strings tolerated: parsed by _to_float
_DATE = {"type": ["string", "null"], "format": "date"}

_ROW_SCHEMA = {
    "type": "object",
    "required": ["date", "instalment_number", "component", "amount"],
    "properties": {
        "date": _DATE,
        "instalment_number": {"type": ["integer", "string", "null"]},
        "component": {"type": ["string", "null"], "enum": [*COMPONENTS, None]},
        "amount": _NUM,
        "balance_after": _NUM,
    },
}

_POSITIONS_SCHEMA = {
    "type": "object",
    "required": ["as_of"],
    "properties": {
        "as_of": {"type": "string", "format": "date"},
        "loans": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "required": ["number", "capital_remaining"],
                "properties": {
                    "number": {"type": ["string", "null"]},
                    "label": {"type": ["string", "null"]},
                    "rate_percent": _NUM,
                    "term_months": {"type": ["integer", "null"]},
                    "capital_granted": _NUM,
                    "start_date": _DATE,
                    "capital_remaining": _NUM,
                    "opening_balance": _NUM,
                    "rows": {"type": ["array", "null"], "items": _ROW_SCHEMA},
                    "next_due_date": _DATE,
                    "next_instalment_number": {"type": ["integer", "null"]},
                    "next_instalment": _NUM,
                    "next_capital": _NUM,
                    "next_interest": _NUM,
                    "next_rate_percent": _NUM,
                    "next_indexante_percent": _NUM,
                    "next_spread_percent": _NUM,
                },
            },
        },
        "funds": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "required": ["account_ref", "value"],
                "properties": {
                    "account_ref": {"type": ["string", "null"]},
                    "holder": {"type": ["string", "null"]},
                    "label": {"type": ["string", "null"]},
                    "units": _NUM,
                    "invested": _NUM,
                    "value": _NUM,
                    "periodic_amount": _NUM,
                    "next_periodic_date": _DATE,
                },
            },
        },
        "balances": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "required": ["kind", "amount"],
                "properties": {
                    "kind": {"type": ["string", "null"], "enum": [*BALANCE_KINDS, None]},
                    "label": {"type": ["string", "null"]},
                    "amount": _NUM,
                },
            },
        },
    },
}

_LOAN_HISTORY_SCHEMA = {
    "type": "object",
    "required": ["rows"],
    "properties": {"rows": {"type": "array", "items": _ROW_SCHEMA}},
}

# --- parsing helpers -----------------------------------------------------

_NON_NUMERIC = re.compile(r"[^0-9,.\-+]")


def _to_float(value) -> Optional[float]:
    """Number or Portuguese-formatted string ('1.234,56', '-62,69 EUR') -> float.
    None/'' -> None; anything unparseable raises ValueError."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"not a number: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"not a number: {value!r}")
    text = _NON_NUMERIC.sub("", value)
    if not text:
        if value.strip():
            raise ValueError(f"not a number: {value!r}")
        return None
    if "," in text:  # Portuguese: '.' thousands, ',' decimal
        text = text.replace(".", "").replace(",", ".")
    return float(text)


def _to_date(value) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def _opt_int(value) -> Optional[int]:
    return int(value) if value is not None else None


def _digits(value) -> str:
    return re.sub(r"\D", "", value or "")


def _parse_row(item: dict) -> ComponentRow:
    component = item["component"]
    if item.get("instalment_number") is None:
        raise PositionExtractionError("row has no instalment number")
    if component not in COMPONENTS:
        raise ValueError(f"unknown component: {component!r}")
    amount = _to_float(item["amount"])
    if amount is None:
        raise ValueError("row without amount")
    balance = _to_float(item.get("balance_after"))
    return ComponentRow(
        date=date.fromisoformat(item["date"]),
        instalment_number=int(item["instalment_number"]),
        component=component,
        amount=abs(amount),
        balance_after=abs(balance) if balance is not None else None,
    )


def _required_float(value) -> float:
    out = _to_float(value)
    if out is None:
        raise ValueError("missing required number")
    return out


def _parse_loan(item: dict) -> ExtractedLoan:
    return ExtractedLoan(
        number=_digits(item["number"]),
        label=item.get("label") or "",
        rate_percent=_to_float(item.get("rate_percent")),
        term_months=_opt_int(item.get("term_months")),
        capital_granted=_to_float(item.get("capital_granted")),
        start_date=_to_date(item.get("start_date")),
        capital_remaining=_required_float(item["capital_remaining"]),
        opening_balance=_to_float(item.get("opening_balance")),
        rows=[_parse_row(r) for r in (item.get("rows") or [])],
        next_due_date=_to_date(item.get("next_due_date")),
        next_instalment_number=_opt_int(item.get("next_instalment_number")),
        next_instalment=_to_float(item.get("next_instalment")),
        next_capital=_to_float(item.get("next_capital")),
        next_interest=_to_float(item.get("next_interest")),
        next_rate_percent=_to_float(item.get("next_rate_percent")),
        next_indexante_percent=_to_float(item.get("next_indexante_percent")),
        next_spread_percent=_to_float(item.get("next_spread_percent")),
    )


def _parse_fund(item: dict) -> ExtractedFund:
    return ExtractedFund(
        account_ref=str(item["account_ref"]),
        holder=item.get("holder"),
        label=item.get("label") or "",
        units=_to_float(item.get("units")),
        invested=_to_float(item.get("invested")),
        value=_required_float(item["value"]),
        periodic_amount=_to_float(item.get("periodic_amount")),
        next_periodic_date=_to_date(item.get("next_periodic_date")),
    )


def _parse_balance(item: dict) -> ExtractedBalance:
    kind = item["kind"]
    if kind not in BALANCE_KINDS:
        raise ValueError(f"unknown balance kind: {kind!r}")
    return ExtractedBalance(kind=kind, label=item.get("label") or "", amount=_required_float(item["amount"]))


_PARSE_ERRORS = (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError)


async def _call(gw, system: str, user: str, schema: dict, file_path: str):
    result = await gw.run(
        VISION_EXTRACTION,
        system,
        user,
        attachments=[build_content_block(file_path)],
        response_schema=schema,
    )
    if result.stop_reason == "max_tokens":
        raise PositionExtractionError(
            "Position response was truncated (max_tokens reached) — document may have too many lines for one call"
        )
    return result


async def extract_statement_positions(file_path: str, gateway=None) -> ExtractedPositions:
    """Extract loans, fund accounts and balances from a consolidated statement.
    A reply that parses but fails reconciliation is returned with `warnings`."""
    gw = gateway or get_gateway()
    result = await _call(gw, _POSITIONS_SYSTEM_PROMPT, _POSITIONS_USER_PROMPT, _POSITIONS_SCHEMA, file_path)
    try:
        data = json.loads(strip_json_fences(result.text))
        positions = ExtractedPositions(
            as_of=date.fromisoformat(data["as_of"]),
            loans=[_parse_loan(i) for i in (data.get("loans") or [])],
            funds=[_parse_fund(i) for i in (data.get("funds") or [])],
            balances=[_parse_balance(i) for i in (data.get("balances") or [])],
            warnings=[],
        )
    except _PARSE_ERRORS as exc:
        raise PositionExtractionError(f"Could not parse position extraction response: {exc}") from exc
    positions.warnings = reconcile(positions)
    return positions


_TEXT_COMPONENTS = {"CAPIT.": "capital", "JUROS": "interest", "SEGURO": "insurance_life", "SEG ED": "insurance_building"}
_CANDIDATE = re.compile(r"^\d{2}-\d{2}-\d{4}\s+\d{2}-\d{2}-\d{4}\b")
_TEXT_ROW = re.compile(
    r"^(\d{2}-\d{2}-\d{4})\s+\d{2}-\d{2}-\d{4}\s+(\S+)\s+PRESTACAO\s*-\s*(.+?)\s+"
    r"(-?[\d.,]+)\s*EUR(?:\s+(-?[\d.,]+)\s*EUR)?\s*$"
)


def parse_loan_history_text(text: str) -> tuple[list[ComponentRow], list[str]]:
    """Rows of a loan-history printout from `pdftotext -raw` output, plus warnings.
    Pure and deterministic; lines that are not table rows are ignored. A balance is
    kept only on capital rows (> 0, or 0.0 on the newest instalment's last capital row)."""
    rows: list[ComponentRow] = []
    warnings: list[str] = []
    skipped = 0
    for line in text.splitlines():
        line = line.strip()
        if not _CANDIDATE.match(line):
            continue
        m = _TEXT_ROW.match(line)
        if not m:
            skipped += 1
            continue
        d, number, name, amount, balance = m.groups()
        component = _TEXT_COMPONENTS.get(re.sub(r"\s+", " ", name.upper()))
        if component is None:
            warnings.append(f"unknown component {name!r} skipped")
            continue
        try:
            when = date(int(d[6:]), int(d[3:5]), int(d[:2]))
            n = int(number)
            value = abs(_to_float(amount))
            bal = abs(_to_float(balance)) if balance is not None else None
        except (ValueError, TypeError):
            skipped += 1
            continue
        rows.append(ComponentRow(when, n, component, value, bal if component == "capital" else None))
    if rows:
        newest = max(r.instalment_number for r in rows)
        for r in rows:  # a 0,00 balance is the payoff only on the newest instalment
            if r.component == "capital" and r.balance_after == 0 and r.instalment_number != newest:
                r.balance_after = None
    if skipped:
        warnings.append(f"{skipped} line(s) starting with dates could not be read (skipped)")
    return rows, warnings


def _pdf_text(file_path: str) -> Optional[str]:
    """`pdftotext -raw` output, or None when the tool is missing / fails."""
    exe = shutil.which("pdftotext")
    if exe is None:
        return None
    try:
        done = subprocess.run([exe, "-raw", file_path, "-"], capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", errors="replace")


async def extract_loan_history(file_path: str, gateway=None, use_text_parser: bool = True) -> ExtractedLoanHistory:
    """Extract the table rows of an online-banking loan-movements printout.
    The text layer is parsed deterministically when possible (no LLM call);
    otherwise the gateway transcribes the document."""
    if use_text_parser:
        text = await asyncio.to_thread(_pdf_text, file_path)
        if text:
            rows, warnings = parse_loan_history_text(text)
            if rows:
                return ExtractedLoanHistory(rows=rows, warnings=warnings + reconcile_loan(rows))
    gw = gateway or get_gateway()
    result = await _call(gw, _LOAN_HISTORY_SYSTEM_PROMPT, _LOAN_HISTORY_USER_PROMPT, _LOAN_HISTORY_SCHEMA, file_path)
    try:
        data = json.loads(strip_json_fences(result.text))
        items = data["rows"]
    except _PARSE_ERRORS as exc:
        raise PositionExtractionError(f"Could not parse loan history response: {exc}") from exc
    rows = []
    for i, item in enumerate(items, 1):
        try:
            rows.append(_parse_row(item))
        except PositionExtractionError:
            raise PositionExtractionError(f"row {i} has no instalment number") from None
        except _PARSE_ERRORS as exc:
            raise PositionExtractionError(f"Could not parse loan history response: {exc}") from exc
    return ExtractedLoanHistory(rows=rows, warnings=reconcile_loan(rows))


# --- deterministic aggregation and reconciliation ------------------------


def aggregate_components(rows: list[ComponentRow]) -> list[Instalment]:
    """Sum the pieces of each instalment number (ordered by number)."""
    groups: dict[int, list[ComponentRow]] = defaultdict(list)
    for r in rows:
        groups[r.instalment_number].append(r)
    out = []
    for number in sorted(groups):
        g = groups[number]
        balances = [r.balance_after for r in g if r.component == "capital" and r.balance_after is not None]
        life = round(sum(r.amount for r in g if r.component == "insurance_life"), 2)
        building = round(sum(r.amount for r in g if r.component == "insurance_building"), 2)
        out.append(Instalment(
            number=number,
            date=min(r.date for r in g),
            capital=round(sum(r.amount for r in g if r.component == "capital"), 2),
            interest=round(sum(r.amount for r in g if r.component == "interest"), 2),
            insurance=round(life + building, 2),
            balance_after=min(balances) if balances else None,
            insurance_life=life, insurance_building=building,
        ))
    return out


def reconcile_loan(rows: list[ComponentRow], opening_balance: Optional[float] = None) -> list[str]:
    """Problems found in one loan's rows; an empty list means consistent."""
    problems: list[str] = []
    for r in rows:
        if r.amount < 0 or (r.balance_after is not None and r.balance_after < 0):
            problems.append(f"negative amount in instalment {r.instalment_number} ({r.component})")

    instalments = aggregate_components(rows)
    for inst in instalments:
        # Interest-only and insurance-only instalments are stored, not failed: interest-only is NOT
        # normal and is raised as a red-flag alert (services/loan_alerts.py); insurance-only is ignored.
        if inst.capital > 0 and inst.interest == 0:
            problems.append(f"incomplete instalment nº {inst.number} (capital without interest)")

    if opening_balance is not None and instalments:
        first = instalments[0]
        if first.balance_after is not None and abs(opening_balance - first.capital - first.balance_after) > TOLERANCE:
            problems.append(
                f"opening balance {opening_balance:.2f} - capital {first.capital:.2f} != "
                f"{first.balance_after:.2f} (instalment {first.number})"
            )

    for prev, cur in zip(instalments, instalments[1:]):
        if cur.number != prev.number + 1 or prev.balance_after is None or cur.balance_after is None:
            continue
        if abs(prev.balance_after - cur.capital - cur.balance_after) > TOLERANCE:
            problems.append(
                f"instalment {cur.number} balance {prev.balance_after:.2f} - capital "
                f"{cur.capital:.2f} != {cur.balance_after:.2f}"
            )
    return problems


def reconcile(positions: ExtractedPositions) -> list[str]:
    """All problems found in an extracted statement; empty means consistent."""
    problems: list[str] = []
    for loan in positions.loans:
        prefix = f"loan {loan.number}: "
        problems += [prefix + p for p in reconcile_loan(loan.rows, loan.opening_balance)]
        if None not in (loan.next_instalment, loan.next_capital, loan.next_interest):
            if abs(loan.next_capital + loan.next_interest - loan.next_instalment) > TOLERANCE:
                problems.append(
                    f"{prefix}next_capital {loan.next_capital:.2f} + next_interest "
                    f"{loan.next_interest:.2f} != next_instalment {loan.next_instalment:.2f}"
                )
        if loan.capital_remaining < 0:
            problems.append(f"{prefix}capital_remaining {loan.capital_remaining:.2f} is negative")
    for fund in positions.funds:
        if fund.value < 0:
            problems.append(f"fund {fund.account_ref}: value {fund.value:.2f} is negative")
    return problems
