"""Positions-only extraction (loans / funds / balances) and the deterministic
aggregation + reconciliation. All data here is synthetic (same shapes as the
real Santander statement and loan-history printouts, invented numbers)."""

import json
from datetime import date

import jsonschema
import pytest

from app.services import position_extraction as pe
from app.services.position_extraction import (
    ComponentRow,
    ExtractedBalance,
    ExtractedFund,
    ExtractedLoan,
    ExtractedPositions,
    PositionExtractionError,
    aggregate_components,
    extract_loan_history,
    extract_statement_positions,
    reconcile,
    reconcile_loan,
)
from tests.fakes.fake_gateway import FakeGateway


def _text_result(text, stop_reason="end_turn"):
    return {"text": text, "stop_reason": stop_reason, "usage": {"input_tokens": 10, "output_tokens": 5}}


def _pdf(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    return str(p)


def _row(d, n, comp, amount, bal=None):
    return ComponentRow(date=d, instalment_number=n, component=comp, amount=amount, balance_after=bal)


# --- statement extraction ------------------------------------------------

_STATEMENT_REPLY = {
    "as_of": "2026-07-31",
    "loans": [{
        "number": "0001.00000000009",
        "label": "CRÉDITO HABITAÇÃO",
        "rate_percent": 2.5,
        "term_months": 300,
        "capital_granted": 5000.0,
        "start_date": "2024-01-15",
        "capital_remaining": 703.60,
        "opening_balance": 1000.0,
        "rows": [
            {"date": "2026-07-02", "instalment_number": 37, "component": "capital", "amount": 241.10, "balance_after": 758.90},
            {"date": "2026-07-02", "instalment_number": 37, "component": "capital", "amount": 55.30, "balance_after": 703.60},
            {"date": "2026-07-02", "instalment_number": 37, "component": "interest", "amount": 262.15, "balance_after": None},
        ],
        "next_due_date": "2026-08-02",
        "next_instalment_number": 38,
        "next_instalment": 571.25,
        "next_capital": 281.40,
        "next_interest": 289.85,
    }],
    "funds": [{
        "account_ref": "12345", "holder": "A. Person", "label": "FUND X", "units": 10.5,
        "invested": 900.0, "value": 1205.40, "periodic_amount": 50.0, "next_periodic_date": "2026-08-05",
    }],
    "balances": [
        {"kind": "deposit", "label": "DEPÓSITOS À ORDEM", "amount": 123.45},
        {"kind": "card", "label": "CARTAO DE CREDITO", "amount": 10.0},
    ],
}


@pytest.mark.asyncio
async def test_statement_happy_path_maps_to_dataclasses(tmp_path):
    fake = FakeGateway([_text_result(json.dumps(_STATEMENT_REPLY))])
    pos = await extract_statement_positions(_pdf(tmp_path), gateway=fake)

    assert pos.as_of == date(2026, 7, 31)
    assert len(pos.loans) == 1
    loan = pos.loans[0]
    assert loan.number == "000100000000009"  # digits only
    assert loan.label == "CRÉDITO HABITAÇÃO"
    assert loan.rate_percent == 2.5 and loan.term_months == 300
    assert loan.start_date == date(2024, 1, 15)
    assert loan.opening_balance == 1000.0 and loan.capital_remaining == 703.60
    assert len(loan.rows) == 3
    assert loan.rows[0] == ComponentRow(date(2026, 7, 2), 37, "capital", 241.10, 758.90)
    assert loan.rows[2].balance_after is None
    assert loan.next_due_date == date(2026, 8, 2)
    assert loan.next_instalment_number == 38
    assert (loan.next_instalment, loan.next_capital, loan.next_interest) == (571.25, 281.40, 289.85)

    fund = pos.funds[0]
    assert isinstance(fund, ExtractedFund)
    assert fund.value == 1205.40 and fund.next_periodic_date == date(2026, 8, 5)
    assert pos.balances == [
        ExtractedBalance("deposit", "DEPÓSITOS À ORDEM", 123.45),
        ExtractedBalance("card", "CARTAO DE CREDITO", 10.0),
    ]
    assert pos.warnings == []  # consistent reply -> no reconciliation problems


@pytest.mark.asyncio
async def test_statement_request_carries_attachment_schema_and_workload(tmp_path):
    fake = FakeGateway([_text_result(json.dumps(_STATEMENT_REPLY))])
    await extract_statement_positions(_pdf(tmp_path), gateway=fake)
    req = fake.requests[0]
    assert req["workload_type"] == "vision_extraction"
    assert len(req["attachments"]) == 1 and req["attachments"][0]["type"] in ("document", "image")
    assert req["response_schema"] == pe._POSITIONS_SCHEMA
    assert "never add rows together" in req["system"].lower() or "never add" in req["system"].lower()
    for label in ("CAPIT", "JUROS", "SEGURO", "SEG ED", "Capital Vincendo", "Saldo em Dívida",
                  "Resumo das Contas", "RESPONSABILIDADES", "Conta Fundo", "Agenda da Conta"):
        assert label in req["system"]


@pytest.mark.asyncio
async def test_statement_validates_against_its_own_schema():
    jsonschema.validate(_STATEMENT_REPLY, pe._POSITIONS_SCHEMA)


@pytest.mark.asyncio
async def test_statement_max_tokens_raises(tmp_path):
    fake = FakeGateway([_text_result("{", stop_reason="max_tokens")])
    with pytest.raises(PositionExtractionError, match="truncated"):
        await extract_statement_positions(_pdf(tmp_path), gateway=fake)


@pytest.mark.asyncio
async def test_statement_non_json_raises(tmp_path):
    fake = FakeGateway([_text_result("sorry, no")])
    with pytest.raises(PositionExtractionError):
        await extract_statement_positions(_pdf(tmp_path), gateway=fake)


@pytest.mark.asyncio
async def test_statement_missing_optional_keys_parse(tmp_path):
    fake = FakeGateway([_text_result(json.dumps({"as_of": "2026-07-31"}))])
    pos = await extract_statement_positions(_pdf(tmp_path), gateway=fake)
    assert pos.loans == [] and pos.funds == [] and pos.balances == []


@pytest.mark.asyncio
async def test_statement_missing_as_of_raises(tmp_path):
    fake = FakeGateway([_text_result(json.dumps({"loans": []}))])
    with pytest.raises(PositionExtractionError):
        await extract_statement_positions(_pdf(tmp_path), gateway=fake)


@pytest.mark.asyncio
async def test_statement_unknown_component_raises(tmp_path):
    bad = json.loads(json.dumps(_STATEMENT_REPLY))
    bad["loans"][0]["rows"][0]["component"] = "fees"
    fake = FakeGateway([_text_result(json.dumps(bad))])
    with pytest.raises(PositionExtractionError):
        await extract_statement_positions(_pdf(tmp_path), gateway=fake)


@pytest.mark.asyncio
async def test_statement_with_problems_returns_warnings(tmp_path):
    bad = json.loads(json.dumps(_STATEMENT_REPLY))
    bad["loans"][0]["next_capital"] = 300.00  # 300 + 289.85 != 571.25
    fake = FakeGateway([_text_result(json.dumps(bad))])
    pos = await extract_statement_positions(_pdf(tmp_path), gateway=fake)
    assert any("next_capital" in w for w in pos.warnings)


@pytest.mark.asyncio
async def test_portuguese_formatted_string_amounts_are_accepted(tmp_path):
    reply = json.loads(json.dumps(_STATEMENT_REPLY))
    reply["loans"][0]["capital_remaining"] = "703,60"
    reply["loans"][0]["rows"][0]["amount"] = "241,10 EUR"
    reply["funds"][0]["value"] = "1.205,40"
    fake = FakeGateway([_text_result(json.dumps(reply))])
    pos = await extract_statement_positions(_pdf(tmp_path), gateway=fake)
    assert pos.loans[0].capital_remaining == 703.60
    assert pos.loans[0].rows[0].amount == 241.10
    assert pos.funds[0].value == 1205.40


def test_to_float_normalises_portuguese_formats():
    f = pe._to_float
    assert f(12) == 12.0
    assert f(12.5) == 12.5
    assert f("1.234,56") == 1234.56
    assert f("-62,69 EUR") == -62.69
    assert f("€ 88.120,14") == 88120.14
    assert f("1234.56") == 1234.56
    assert f(None) is None
    assert f("") is None
    with pytest.raises(ValueError):
        f("abc")


@pytest.mark.asyncio
async def test_component_amounts_are_made_positive(tmp_path):
    reply = {"rows": [
        {"date": "2026-10-02", "instalment_number": 38, "component": "interest", "amount": "-62,69"},
    ]}
    fake = FakeGateway([_text_result(json.dumps(reply))])
    hist = await extract_loan_history(_pdf(tmp_path), gateway=fake)
    assert hist.rows[0].amount == 62.69


# --- loan history printout -----------------------------------------------


@pytest.mark.asyncio
async def test_loan_history_parses_printout_rows(tmp_path):
    reply = {"rows": [
        {"date": "2026-09-02", "instalment_number": 289, "component": "interest", "amount": 84.15, "balance_after": None},
        {"date": "2026-09-02", "instalment_number": 289, "component": "capital", "amount": 203.40, "balance_after": 18420.55},
        {"date": "2026-09-02", "instalment_number": 289, "component": "insurance_life", "amount": 4.10, "balance_after": None},
        {"date": "2026-09-02", "instalment_number": 289, "component": "insurance_building", "amount": 6.20, "balance_after": None},
    ]}
    fake = FakeGateway([_text_result(json.dumps(reply))])
    hist = await extract_loan_history(_pdf(tmp_path), gateway=fake)

    req = fake.requests[0]
    assert req["workload_type"] == "vision_extraction"
    assert req["response_schema"] == pe._LOAN_HISTORY_SCHEMA
    assert len(req["attachments"]) == 1
    assert "Consulta Movimentos" in req["system"] or "PRESTA" in req["system"]

    assert len(hist.rows) == 4
    assert hist.rows[1] == ComponentRow(date(2026, 9, 2), 289, "capital", 203.40, 18420.55)
    assert hist.warnings == []
    inst = aggregate_components(hist.rows)
    assert len(inst) == 1
    assert inst[0].capital == 203.40 and inst[0].interest == 84.15
    assert inst[0].insurance == pytest.approx(10.30)
    assert inst[0].balance_after == 18420.55


@pytest.mark.asyncio
async def test_loan_history_max_tokens_and_bad_json_raise(tmp_path):
    with pytest.raises(PositionExtractionError, match="truncated"):
        await extract_loan_history(_pdf(tmp_path), gateway=FakeGateway([_text_result("{", "max_tokens")]))
    with pytest.raises(PositionExtractionError):
        await extract_loan_history(_pdf(tmp_path), gateway=FakeGateway([_text_result("nope")]))
    with pytest.raises(PositionExtractionError):  # missing required "rows"
        await extract_loan_history(_pdf(tmp_path), gateway=FakeGateway([_text_result("{}")]))


@pytest.mark.asyncio
async def test_loan_history_with_inconsistent_rows_returns_warnings(tmp_path):
    reply = {"rows": [
        {"date": "2026-08-02", "instalment_number": 1, "component": "capital", "amount": 100.0, "balance_after": 900.0},
        {"date": "2026-08-02", "instalment_number": 1, "component": "interest", "amount": 10.0},
        {"date": "2026-09-02", "instalment_number": 2, "component": "capital", "amount": 100.0, "balance_after": 750.0},
        {"date": "2026-09-02", "instalment_number": 2, "component": "interest", "amount": 9.0},
    ]}
    hist = await extract_loan_history(_pdf(tmp_path), gateway=FakeGateway([_text_result(json.dumps(reply))]))
    assert hist.warnings and "instalment 2" in hist.warnings[0]


# --- aggregate_components ------------------------------------------------


def test_aggregate_sums_pieces_of_one_instalment():
    rows = [
        _row(date(2026, 8, 3), 35, "interest", 280.95),
        _row(date(2026, 8, 5), 35, "capital", 205.96, 1000.00 - 205.96),
        _row(date(2026, 8, 2), 35, "capital", 80.00, 700.00),
        _row(date(2026, 8, 9), 35, "capital", 19.76, 674.28),
    ]
    out = aggregate_components(rows)
    assert len(out) == 1
    inst = out[0]
    assert inst.number == 35
    assert inst.capital == pytest.approx(305.72)
    assert inst.interest == pytest.approx(280.95)
    assert inst.insurance == 0.0
    assert inst.date == date(2026, 8, 2)  # earliest piece
    assert inst.balance_after == 674.28  # lowest-balance capital row


def test_aggregate_sums_insurance_and_orders_by_number():
    rows = [
        _row(date(2026, 9, 2), 38, "capital", 10.0, 90.0),
        _row(date(2026, 8, 2), 37, "capital", 10.0, 100.0),
        _row(date(2026, 8, 2), 37, "insurance_life", 3.5),
        _row(date(2026, 8, 2), 37, "insurance_building", 1.25),
    ]
    out = aggregate_components(rows)
    assert [i.number for i in out] == [37, 38]
    assert out[0].insurance == pytest.approx(4.75)
    assert out[0].interest == 0.0


def test_aggregate_carries_both_insurance_parts():
    rows = [
        _row(date(2026, 8, 2), 37, "capital", 10.0, 100.0),
        _row(date(2026, 8, 2), 37, "insurance_life", 3.5),
        _row(date(2026, 8, 2), 37, "insurance_building", 1.25),
        _row(date(2026, 9, 2), 38, "capital", 10.0, 90.0),
    ]
    first, second = aggregate_components(rows)
    assert (first.insurance_life, first.insurance_building) == (3.5, 1.25)
    assert first.insurance == pytest.approx(first.insurance_life + first.insurance_building)
    assert (second.insurance_life, second.insurance_building, second.insurance) == (0.0, 0.0, 0.0)


def test_aggregate_balance_none_when_no_capital_balance():
    out = aggregate_components([_row(date(2026, 8, 2), 1, "interest", 5.0)])
    assert out[0].balance_after is None and out[0].capital == 0.0


# --- reconcile_loan ------------------------------------------------------


def _series(balances=(900.0, 800.0, 700.0)):
    rows = []
    for i, bal in enumerate(balances):
        n = 10 + i
        d = date(2026, 5 + i, 2)
        rows.append(_row(d, n, "capital", 100.0, bal))
        rows.append(_row(d, n, "interest", 20.0))
    return rows


def test_reconcile_loan_continuity_passes():
    assert reconcile_loan(_series()) == []
    assert reconcile_loan(_series(), opening_balance=1000.0) == []


def test_reconcile_loan_continuity_within_tolerance():
    assert reconcile_loan(_series((900.0, 800.01, 700.02))) == []


def test_reconcile_loan_flags_broken_continuity():
    problems = reconcile_loan(_series((900.0, 800.0, 650.0)))
    assert len(problems) == 1
    assert problems[0] == "instalment 12 balance 800.00 - capital 100.00 != 650.00"


def test_reconcile_loan_skips_non_consecutive_and_missing_balances():
    rows = _series()
    rows = [r for r in rows if r.instalment_number != 11]  # gap 10 -> 12
    assert reconcile_loan(rows) == []
    rows2 = _series((900.0, None, 700.0))
    assert reconcile_loan(rows2) == []


def test_reconcile_loan_opening_balance_check():
    problems = reconcile_loan(_series(), opening_balance=1100.0)
    assert len(problems) == 1 and "opening balance 1100.00" in problems[0] and "instalment 10" in problems[0]


def test_reconcile_loan_flags_negative_amount():
    rows = [_row(date(2026, 8, 2), 1, "capital", -5.0), _row(date(2026, 8, 2), 1, "interest", 2.0)]
    assert any("negative" in p for p in reconcile_loan(rows))


def test_reconcile_loan_flags_incomplete_instalment():
    rows = [_row(date(2026, 8, 2), 7, "capital", 50.0, 500.0)]
    assert reconcile_loan(rows) == ["incomplete instalment nº 7 (capital without interest)"]


def test_reconcile_loan_interest_only_and_insurance_only_are_stored_not_failed():
    # Interest-only is NOT normal (it is raised as a red-flag alert by loan_alerts), but it is not a
    # reconciliation failure either: the data is stored and flagged.
    rows = [_row(date(2026, 8, 2), 8, "interest", 5.0), _row(date(2026, 9, 2), 9, "insurance_life", 3.0)]
    assert reconcile_loan(rows) == []


def test_reconcile_loan_interest_only_series_then_amortising():
    # still reconciles (stored); each interest-only instalment also raises an alert elsewhere
    # capital grace period: interest rows only (no balances), then capital starts
    rows = [_row(date(2026, m, 2), m, "interest", 250.0) for m in range(1, 6)]
    rows += [_row(date(2026, 6, 2), 6, "capital", 100.0, 900.0), _row(date(2026, 6, 2), 6, "interest", 250.0)]
    rows += [_row(date(2026, 7, 2), 7, "capital", 100.0, 800.0), _row(date(2026, 7, 2), 7, "interest", 249.0)]
    assert reconcile_loan(rows) == []
    assert reconcile_loan(rows, opening_balance=1000.0) == []


# --- reconcile (whole statement) -----------------------------------------


def _loan(**over):
    base = dict(
        number="000100000000009", label="CRÉDITO HABITAÇÃO", rate_percent=2.5, term_months=300,
        capital_granted=5000.0, start_date=None, capital_remaining=703.60, opening_balance=None,
        rows=[], next_due_date=None, next_instalment_number=None, next_instalment=None,
        next_capital=None, next_interest=None,
    )
    base.update(over)
    return ExtractedLoan(**base)


def _positions(loans=(), funds=()):
    return ExtractedPositions(as_of=date(2026, 7, 31), loans=list(loans), funds=list(funds), balances=[], warnings=[])


def test_reconcile_flags_next_instalment_mismatch():
    pos = _positions([_loan(next_instalment=571.25, next_capital=300.0, next_interest=289.85)])
    problems = reconcile(pos)
    assert len(problems) == 1
    assert problems[0].startswith("loan 000100000000009: ") and "next_capital" in problems[0]


def test_reconcile_accepts_consistent_next_instalment_and_prefixes_loan_problems():
    ok = _loan(next_instalment=571.25, next_capital=281.40, next_interest=289.85)
    assert reconcile(_positions([ok])) == []
    rows = _series((900.0, 800.0, 650.0))
    problems = reconcile(_positions([_loan(rows=rows)]))
    assert problems == ["loan 000100000000009: instalment 12 balance 800.00 - capital 100.00 != 650.00"]


def test_reconcile_uses_opening_balance_and_flags_negative_remaining_and_fund():
    loan = _loan(rows=_series(), opening_balance=1100.0, capital_remaining=-1.0)
    fund = ExtractedFund("1", None, "F", None, None, -3.0, None, None)
    problems = reconcile(_positions([loan], [fund]))
    assert any("opening balance" in p for p in problems)
    assert any("capital_remaining" in p for p in problems)
    assert any("fund 1" in p and "value" in p for p in problems)


# --- I2: revised (next-instalment) rate -----------------------------------


@pytest.mark.asyncio
async def test_next_rate_percent_is_parsed_and_optional(tmp_path):
    reply = json.loads(json.dumps(_STATEMENT_REPLY))
    reply["loans"][0]["next_rate_percent"] = "3,500"
    pos = await extract_statement_positions(_pdf(tmp_path), gateway=FakeGateway([_text_result(json.dumps(reply))]))
    assert pos.loans[0].next_rate_percent == 3.5 and pos.loans[0].rate_percent == 2.5
    pos = await extract_statement_positions(
        _pdf(tmp_path), gateway=FakeGateway([_text_result(json.dumps(_STATEMENT_REPLY))]))
    assert pos.loans[0].next_rate_percent is None


def test_next_rate_in_schema_and_prompt():
    props = pe._POSITIONS_SCHEMA["properties"]["loans"]["items"]["properties"]
    assert "next_rate_percent" in props and "null" in props["next_rate_percent"]["type"]
    assert "next_rate_percent" in pe._POSITIONS_SYSTEM_PROMPT
    assert "Próxima Prestação" in pe._POSITIONS_SYSTEM_PROMPT and "TAN" in pe._POSITIONS_SYSTEM_PROMPT


# --- Block A: indexante / spread -----------------------------------------


@pytest.mark.asyncio
async def test_indexante_and_spread_are_parsed_and_optional(tmp_path):
    reply = json.loads(json.dumps(_STATEMENT_REPLY))
    reply["loans"][0]["next_indexante_percent"] = "1,800"
    reply["loans"][0]["next_spread_percent"] = 1.2
    pos = await extract_statement_positions(_pdf(tmp_path), gateway=FakeGateway([_text_result(json.dumps(reply))]))
    assert pos.loans[0].next_indexante_percent == 1.8 and pos.loans[0].next_spread_percent == 1.2
    pos = await extract_statement_positions(
        _pdf(tmp_path), gateway=FakeGateway([_text_result(json.dumps(_STATEMENT_REPLY))]))
    assert pos.loans[0].next_indexante_percent is None and pos.loans[0].next_spread_percent is None


def test_indexante_and_spread_in_schema_and_prompt():
    props = pe._POSITIONS_SCHEMA["properties"]["loans"]["items"]["properties"]
    for key in ("next_indexante_percent", "next_spread_percent"):
        assert key in props and "null" in props[key]["type"]
        assert key in pe._POSITIONS_SYSTEM_PROMPT
    assert "Indexante" in pe._POSITIONS_SYSTEM_PROMPT and "Spread" in pe._POSITIONS_SYSTEM_PROMPT
