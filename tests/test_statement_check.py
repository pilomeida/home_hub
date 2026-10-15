"""Does a statement add up? (decision D17). Statement bodies are the JSON the gateway returns, read by the
real parser (`extract_statement_transactions`), and the handler tests run the real pipeline."""

import json
from datetime import date

import pytest
from sqlmodel import select

import app.services.extraction as extraction
from app.domains.financials import handler as pipeline
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import StatementCheck, Transaction
from app.services.extraction import (
    _STATEMENT_SCHEMA, _STATEMENT_SYSTEM_PROMPT, extract_statement_transactions,
)
from app.services.statement_check import (
    MAX_FLAGGED, apply_statement_check, check_extracted, money,
)
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result

NEW_STATEMENT_FIELDS = ("opening_balance", "closing_balance")
NEW_LINE_FIELDS = ("page", "balance_after", "unclear")


# --- statement bodies in the shape the gateway returns --------------------------------------------------

def _line(day, description, amount, kind, *, page=1, balance_after=None, unclear=None, hint="other_expense"):
    item = {"date": f"2026-07-{day:02d}", "description": description, "amount": amount, "currency": "EUR",
            "type": kind, "category_hint": hint}
    if page is not None:
        item["page"] = page
    if balance_after is not None:
        item["balance_after"] = balance_after
    if unclear is not None:
        item["unclear"] = unclear
    return item


def _body(lines, opening=1000.00, closing=2980.54, **extra):
    body = {"statement_period": "2026-07", "transactions": lines}
    if opening is not None:
        body["opening_balance"] = opening
    if closing is not None:
        body["closing_balance"] = closing
    body.update(extra)
    return body


def good_lines():
    """A realistic month: purchases, salary, a transfer out and one in (told apart by the bank's own words)."""
    return [
        _line(2, "CONTINENTE MAFRA", 42.15, "debit", balance_after=957.85, hint="groceries"),
        _line(5, "SALARIO EMPRESA X", 2200.00, "credit", balance_after=3157.85, hint="income"),
        _line(8, "EDP COMERCIAL", 87.32, "debit", balance_after=3070.53, hint="electricity"),
        _line(12, "TRF.IMED. P/ REVOLUT", 100.00, "transfer", page=2, balance_after=2970.53, hint="transfer"),
        _line(15, "MEO SERVICOS", 39.99, "debit", page=2, balance_after=2930.54, hint="telecom"),
        _line(20, "TRF.IMED. DE MARIA SANTOS", 50.00, "transfer", page=2, balance_after=2980.54, hint="transfer"),
    ]


async def _extract(tmp_path, body):
    pdf = tmp_path / "statement.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    text = body if isinstance(body, str) else json.dumps(body)
    return await extract_statement_transactions(str(pdf), gateway=FakeGateway([gateway_text_result(text)]))


async def _verdict(tmp_path, body):
    return check_extracted(await _extract(tmp_path, body))


# --- the check, pure --------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_statement_that_adds_up_is_ok_and_holds_nothing(tmp_path):
    v = await _verdict(tmp_path, _body(good_lines()))
    assert v.status == "ok"
    assert v.flagged == {}
    assert v.lines_total == pytest.approx(1980.54)
    assert v.difference == pytest.approx(0.0)
    assert "1.000,00" in v.note and "2.980,54" in v.note


@pytest.mark.asyncio
async def test_credit_debit_and_transfer_signs_in_a_realistic_mix(tmp_path):
    # without any running balance the signs alone must still add up: credits +, debits -, transfers by words
    lines = [dict(l) for l in good_lines()]
    for l in lines:
        l.pop("balance_after")
    v = await _verdict(tmp_path, _body(lines))
    assert v.status == "ok"
    assert v.lines_total == pytest.approx(2200.00 + 50.00 - 42.15 - 87.32 - 100.00 - 39.99)


@pytest.mark.asyncio
async def test_a_transfer_direction_is_found_from_the_running_balance_when_the_words_are_silent(tmp_path):
    lines = [
        _line(2, "TRANSFERENCIA INTERNA", 300.00, "transfer", balance_after=700.00),
        _line(9, "TRANSFERENCIA INTERNA", 100.00, "transfer", balance_after=800.00),
    ]
    v = await _verdict(tmp_path, _body(lines, opening=1000.00, closing=800.00))
    assert v.status == "ok"
    assert v.lines_total == pytest.approx(-200.00)


@pytest.mark.asyncio
async def test_a_transfer_with_no_findable_direction_makes_the_statement_unverified_not_wrong(tmp_path):
    lines = [_line(2, "TRANSFERENCIA INTERNA", 300.00, "transfer"),
             _line(3, "CONTINENTE", 10.00, "debit")]
    v = await _verdict(tmp_path, _body(lines, opening=1000.00, closing=690.00))
    assert v.status == "unverified"
    assert v.flagged == {}
    assert "transfer" in v.note and "in or out" in v.note


@pytest.mark.asyncio
async def test_mismatch_is_localised_by_the_running_balance_to_the_right_line(tmp_path):
    lines = good_lines()
    lines[2]["amount"] = 78.32            # the EDP line misread: 87.32 -> 78.32; its printed balance is right
    v = await _verdict(tmp_path, _body(lines))
    assert v.status == "mismatch"
    assert list(v.flagged) == [2]
    assert "3.070,53" in v.flagged[2]
    assert "Held out of the totals" in v.flagged[2]
    assert v.difference == pytest.approx(-9.00)
    assert "9,00 more than the closing balance" in v.note
    assert "difference 9,00" in v.note


@pytest.mark.asyncio
async def test_a_missed_line_shows_at_the_line_after_it(tmp_path):
    lines = good_lines()
    del lines[3]                          # the 100,00 transfer was not read at all
    v = await _verdict(tmp_path, _body(lines))
    assert v.status == "mismatch"
    assert v.difference == pytest.approx(-100.00)
    assert "100,00 more than the closing balance" in v.note
    assert list(v.flagged) == [3]         # the MEO line: its balance no longer follows from the one before
    assert "MEO" not in v.flagged[3]      # reasons speak plainly, they do not quote data


@pytest.mark.asyncio
async def test_lines_without_a_printed_balance_share_the_blame(tmp_path):
    lines = good_lines()
    lines[2]["amount"] = 78.32
    lines[2].pop("balance_after")         # no balance printed after the (misread) EDP line
    v = await _verdict(tmp_path, _body(lines))
    assert v.status == "mismatch"
    # the next printed balance (after the transfer) does not follow: it is either of the two lines since the
    # last balance that did
    assert sorted(v.flagged) == [2, 3]
    assert "within these 2 lines" in v.flagged[2]


@pytest.mark.asyncio
async def test_mismatch_that_cannot_be_localised_holds_no_line_but_is_recorded(tmp_path):
    lines = [dict(l) for l in good_lines()]
    for l in lines:
        l.pop("balance_after")            # a statement that prints no running balance
    lines[2]["amount"] = 78.32
    v = await _verdict(tmp_path, _body(lines))
    assert v.status == "mismatch"
    assert v.flagged == {}
    assert "could not tell which line is wrong" in v.note
    assert "(difference 9,00)" in v.note


@pytest.mark.asyncio
async def test_a_closing_balance_that_disagrees_with_the_last_printed_balance_is_not_blamed_on_a_line(tmp_path):
    v = await _verdict(tmp_path, _body(good_lines(), closing=2990.54))
    assert v.status == "mismatch"
    assert v.flagged == {}
    assert "could not tell which line is wrong" in v.note


@pytest.mark.asyncio
async def test_trailing_lines_after_the_last_printed_balance_are_blamed_for_a_closing_mismatch(tmp_path):
    lines = good_lines()
    lines[5].pop("balance_after")
    v = await _verdict(tmp_path, _body(lines, closing=2990.54))
    assert v.status == "mismatch"
    assert list(v.flagged) == [5]
    assert "closing balance" in v.flagged[5]


@pytest.mark.asyncio
async def test_a_statement_printed_newest_first_is_localised_on_the_right_line(tmp_path):
    lines = good_lines()
    lines[2]["amount"] = 78.32
    v = await _verdict(tmp_path, _body(lines[::-1]))
    assert v.status == "mismatch"
    assert list(v.flagged) == [len(lines) - 1 - 2]


@pytest.mark.asyncio
async def test_a_newest_first_statement_that_adds_up_is_ok(tmp_path):
    v = await _verdict(tmp_path, _body(good_lines()[::-1]))
    assert v.status == "ok"


@pytest.mark.asyncio
async def test_exactly_the_limit_of_doubtful_lines_is_held_one_more_holds_none(tmp_path):
    def many(n_unclear):
        return [_line(1 + i % 28, f"SHOP {i}", 10.00, "debit", unclear=i < n_unclear) for i in range(30)]
    at_limit = await _verdict(tmp_path, _body(many(MAX_FLAGGED), opening=1000.00, closing=650.00))
    assert at_limit.status == "mismatch"
    assert len(at_limit.flagged) == MAX_FLAGGED
    over = await _verdict(tmp_path, _body(many(MAX_FLAGGED + 1), opening=1000.00, closing=650.00))
    assert over.status == "mismatch"
    assert over.flagged == {}
    assert "too many to single out" in over.note


@pytest.mark.asyncio
async def test_a_break_spread_over_more_than_the_limit_of_lines_flags_none(tmp_path):
    # 30 lines and no printed balance until the very last one, which does not follow: any of 30 could be wrong
    lines = [_line(1 + i % 28, f"SHOP {i}", 10.00, "debit") for i in range(30)]
    lines[-1]["balance_after"] = 705.00        # should be 700.00
    v = await _verdict(tmp_path, _body(lines, opening=1000.00, closing=705.00))
    assert v.status == "mismatch"
    assert v.flagged == {}
    assert "30 lines are doubtful" in v.note


@pytest.mark.asyncio
async def test_unclear_lines_are_flagged_with_a_plain_reason_when_the_statement_does_not_add_up(tmp_path):
    lines = good_lines()
    lines[0]["unclear"] = True
    lines[2]["amount"] = 78.32
    v = await _verdict(tmp_path, _body(lines))
    assert sorted(v.flagged) == [0, 2]
    assert "isn't sure it read this line correctly" in v.flagged[0]
    assert "42,15" in v.flagged[0]
    assert "3.070,53" in v.flagged[2]


@pytest.mark.asyncio
async def test_an_unclear_line_in_a_statement_that_adds_up_holds_nothing(tmp_path):
    lines = good_lines()
    lines[0]["unclear"] = True
    v = await _verdict(tmp_path, _body(lines))
    assert v.status == "ok"
    assert v.flagged == {}


@pytest.mark.asyncio
async def test_an_unclear_line_in_an_unverified_statement_is_held_because_nothing_can_prove_it(tmp_path):
    lines = [dict(l) for l in good_lines()]
    for l in lines:
        l.pop("balance_after")
    lines[4]["unclear"] = True
    v = await _verdict(tmp_path, _body(lines, opening=None, closing=None))
    assert v.status == "unverified"
    assert list(v.flagged) == [4]
    assert "held back" in v.note


@pytest.mark.asyncio
async def test_missing_opening_balance_only_is_unverified_and_holds_nothing(tmp_path):
    v = await _verdict(tmp_path, _body(good_lines(), opening=None))
    assert v.status == "unverified"
    assert v.opening_balance is None and v.closing_balance == pytest.approx(2980.54)
    assert v.difference is None
    assert v.flagged == {}
    assert "no opening balance" in v.note


@pytest.mark.asyncio
async def test_old_body_shape_without_any_balance_works_and_is_unverified_with_nothing_flagged(tmp_path):
    old = {"statement_period": "2026-07", "transactions": [
        {"date": "2026-07-05", "description": "CONTINENTE MAFRA", "amount": 42.15, "currency": "EUR",
         "type": "debit", "category_hint": "groceries"},
        {"date": "2026-07-10", "description": "SALARIO EMPRESA X", "amount": 2200.00, "currency": "EUR",
         "type": "credit", "category_hint": "income"}]}
    extracted = await _extract(tmp_path, old)
    assert extracted.opening_balance is None and extracted.closing_balance is None
    assert [(t.page, t.balance_after, t.unclear) for t in extracted.transactions] == [(None, None, False)] * 2
    v = check_extracted(extracted)
    assert v.status == "unverified" and v.flagged == {}
    assert "no opening or closing balance" in v.note


@pytest.mark.asyncio
async def test_a_cent_is_a_mismatch_and_less_than_half_a_cent_is_not(tmp_path):
    assert (await _verdict(tmp_path, _body(good_lines(), closing=2980.544))).status == "ok"
    assert (await _verdict(tmp_path, _body(good_lines(), closing=2980.55))).status == "mismatch"


def test_money_is_written_the_way_the_statements_print_it():
    assert money(1200.0) == "1.200,00"
    assert money(52.0) == "52,00"
    assert money(-3.5) == "-3,50"
    assert money(1234567.891) == "1.234.567,89"


# --- extraction: prompt, schema, tolerant parser ------------------------------------------------------------

def test_prompt_and_schema_carry_the_new_optional_fields_and_none_is_required():
    for name in (*NEW_STATEMENT_FIELDS, *NEW_LINE_FIELDS):
        assert f'"{name}"' in _STATEMENT_SYSTEM_PROMPT
    assert "never work it out" in _STATEMENT_SYSTEM_PROMPT and "Do NOT guess" in _STATEMENT_SYSTEM_PROMPT
    props = _STATEMENT_SCHEMA["properties"]
    item = props["transactions"]["items"]
    for name in NEW_STATEMENT_FIELDS:
        assert name in props and "null" in props[name]["type"]
        assert name not in _STATEMENT_SCHEMA["required"]
    for name in NEW_LINE_FIELDS:
        assert name in item["properties"] and "null" in item["properties"][name]["type"]
        assert name not in item["required"]
    assert _STATEMENT_SCHEMA["required"] == ["transactions"]
    assert item["required"] == ["date", "description", "amount", "type"]


@pytest.mark.asyncio
async def test_the_request_sends_the_extended_schema_and_bodies_validate_against_it(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    pdf = tmp_path / "statement.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    bodies = [_body(good_lines()), _body(good_lines(), opening=None, closing=None),
              {"statement_period": None, "transactions": []},
              _body([_line(2, "X", 1.0, "debit", page=None, unclear=None)])]
    for body in bodies:
        jsonschema.validate(body, _STATEMENT_SCHEMA)
    fake = FakeGateway([gateway_text_result(json.dumps(bodies[0]))])
    await extract_statement_transactions(str(pdf), gateway=fake)
    assert fake.requests[0]["response_schema"] == _STATEMENT_SCHEMA


@pytest.mark.asyncio
async def test_parser_reads_the_new_fields(tmp_path):
    extracted = await _extract(tmp_path, _body(good_lines()))
    assert extracted.opening_balance == 1000.00 and extracted.closing_balance == 2980.54
    assert [t.page for t in extracted.transactions] == [1, 1, 1, 2, 2, 2]
    assert extracted.transactions[0].balance_after == 957.85
    assert not any(t.unclear for t in extracted.transactions)


@pytest.mark.asyncio
async def test_parser_tolerates_nulls_missing_strings_and_junk_in_every_new_field(tmp_path):
    body = {
        "statement_period": "2026-07",
        "opening_balance": "1000.00",          # a number as text, like the existing amount parsing allows
        "closing_balance": "n/a",              # junk: not printed
        "transactions": [
            {"date": "2026-07-02", "description": "A", "amount": 1.0, "type": "debit",
             "page": "2", "balance_after": "999.00", "unclear": "true"},
            {"date": "2026-07-03", "description": "B", "amount": 1.0, "type": "debit",
             "page": None, "balance_after": None, "unclear": None},
            {"date": "2026-07-04", "description": "C", "amount": 1.0, "type": "debit"},
            {"date": "2026-07-05", "description": "D", "amount": 1.0, "type": "debit",
             "page": 0, "balance_after": True, "unclear": "maybe"},
            {"date": "2026-07-06", "description": "E", "amount": 1.0, "type": "debit",
             "page": 1.5, "balance_after": "NaN", "unclear": 7},
            {"date": "2026-07-07", "description": "F", "amount": 1.0, "type": "debit",
             "page": 3.0, "balance_after": -12.5, "unclear": False},
        ],
    }
    extracted = await _extract(tmp_path, body)
    assert extracted.opening_balance == 1000.00 and extracted.closing_balance is None
    got = [(t.page, t.balance_after, t.unclear) for t in extracted.transactions]
    assert got == [(2, 999.00, True), (None, None, False), (None, None, False),
                   (None, None, False), (None, None, False), (3, -12.5, False)]


@pytest.mark.asyncio
async def test_a_missing_required_field_still_fails_the_statement_as_before(tmp_path):
    bad = {"statement_period": "2026-07", "opening_balance": 1.0,
           "transactions": [{"date": "2026-07-02", "description": "A", "type": "debit"}]}
    with pytest.raises(extraction.StatementExtractionError):
        await _extract(tmp_path, bad)


# --- the handler: persisting the check -------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_merchant_classification(monkeypatch):
    class _NoMerchant:
        created_new_merchant = False
        merchant = None

    async def _noop(session, transaction, gateway=None):
        return _NoMerchant()
    monkeypatch.setattr(pipeline, "classify_transaction", _noop)

    async def _statement(file_path, gateway=None):
        return "statement"
    monkeypatch.setattr(pipeline, "classify_document", _statement)


def _document(session, tmp_path, name="statement.pdf"):
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4 fake")
    doc = Document(filename=name, file_path=str(path), content_hash=f"hash-{name}",
                   source=DocumentSource.MANUAL, status=DocumentStatus.PENDING)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


async def _process(session, tmp_path, monkeypatch, body, name="statement.pdf"):
    fake = FakeGateway([gateway_text_result(json.dumps(body))])
    monkeypatch.setattr(extraction, "get_gateway", lambda: fake)
    doc = _document(session, tmp_path, name)
    result = await pipeline.process_financials_document(session, doc)
    return doc, result


def _rows(session, doc):
    return session.exec(select(Transaction).where(Transaction.document_id == doc.id)
                        .order_by(Transaction.id)).all()


def _check_row(session, doc):
    return session.exec(select(StatementCheck).where(StatementCheck.document_id == doc.id)).all()


@pytest.mark.asyncio
async def test_handler_ok_statement_stores_pages_and_an_ok_check_and_holds_nothing(session, tmp_path, monkeypatch):
    doc, result = await _process(session, tmp_path, monkeypatch, _body(good_lines()))
    assert result.status == DocumentStatus.PROCESSED
    rows = _rows(session, doc)
    assert [r.source_page for r in rows] == [1, 1, 1, 2, 2, 2]
    assert all(r.review_status is None and r.review_reason is None for r in rows)
    (check,) = _check_row(session, doc)
    assert (check.status, check.opening_balance, check.closing_balance) == ("ok", 1000.00, 2980.54)
    assert check.lines_total == pytest.approx(1980.54) and check.difference == pytest.approx(0.0)
    assert "Adds up" in check.note


@pytest.mark.asyncio
async def test_handler_holds_only_the_localised_line_and_records_the_mismatch(session, tmp_path, monkeypatch):
    lines = good_lines()
    lines[2]["amount"] = 78.32
    doc, result = await _process(session, tmp_path, monkeypatch, _body(lines))
    assert result.status == DocumentStatus.PROCESSED
    rows = _rows(session, doc)
    assert [r.review_status for r in rows] == [None, None, "needs_check", None, None, None]
    assert rows[2].description == "EDP COMERCIAL" and "3.070,53" in rows[2].review_reason
    (check,) = _check_row(session, doc)
    assert check.status == "mismatch" and check.difference == pytest.approx(-9.00)
    assert "Doesn't add up" in check.note
    # a held line counts nowhere (Transaction.counts()), the others still do
    counted = session.exec(select(Transaction).where(Transaction.document_id == doc.id,
                                                     Transaction.counts())).all()
    assert len(counted) == 5


@pytest.mark.asyncio
async def test_handler_unlocalisable_mismatch_holds_no_line(session, tmp_path, monkeypatch):
    lines = [dict(l) for l in good_lines()]
    for l in lines:
        l.pop("balance_after")
    lines[0]["amount"] = 52.15
    doc, _ = await _process(session, tmp_path, monkeypatch, _body(lines))
    assert all(r.review_status is None for r in _rows(session, doc))
    (check,) = _check_row(session, doc)
    assert check.status == "mismatch"


@pytest.mark.asyncio
async def test_handler_old_body_without_balances_behaves_as_before(session, tmp_path, monkeypatch):
    old = {"statement_period": "2026-07", "transactions": [
        {"date": "2026-07-05", "description": "CONTINENTE MAFRA", "amount": 42.15, "currency": "EUR",
         "type": "debit", "category_hint": "groceries"},
        {"date": "2026-07-10", "description": "SALARIO EMPRESA X", "amount": 2200.00, "currency": "EUR",
         "type": "credit", "category_hint": "income"}]}
    doc, result = await _process(session, tmp_path, monkeypatch, old)
    assert result.status == DocumentStatus.PROCESSED
    rows = _rows(session, doc)
    assert len(rows) == 2
    assert all(r.review_status is None and r.source_page is None for r in rows)
    (check,) = _check_row(session, doc)
    assert check.status == "unverified" and check.difference is None


@pytest.mark.asyncio
async def test_handler_unclear_line_is_held_in_a_statement_with_a_mismatch_only(session, tmp_path, monkeypatch):
    lines = good_lines()
    lines[0]["unclear"] = True
    lines[0]["amount"] = 142.15      # misread because smudged; the printed balance (957,85) exposes it too
    doc, _ = await _process(session, tmp_path, monkeypatch, _body(lines))
    rows = _rows(session, doc)
    assert rows[0].review_status == "needs_check"
    assert "isn't sure it read this line correctly" in rows[0].review_reason
    assert "957,85" in rows[0].review_reason


@pytest.mark.asyncio
async def test_a_failure_while_checking_undoes_the_lines_like_any_other_failure(session, tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("check exploded")
    monkeypatch.setattr(pipeline, "apply_statement_check", boom)
    doc, result = await _process(session, tmp_path, monkeypatch, _body(good_lines()))
    assert result.status == DocumentStatus.NEEDS_ATTENTION
    assert _rows(session, doc) == [] and _check_row(session, doc) == []


@pytest.mark.asyncio
async def test_withdrawing_a_statement_removes_its_check(session, tmp_path, monkeypatch):
    doc, _ = await _process(session, tmp_path, monkeypatch, _body(good_lines()))
    assert len(_check_row(session, doc)) == 1
    await pipeline.FinancialsHandler().withdraw(session, doc)
    assert _check_row(session, doc) == [] and _rows(session, doc) == []


# --- re-processing -------------------------------------------------------------------------------------------

async def _first_run_with_a_held_line(session, tmp_path, monkeypatch):
    lines = good_lines()
    lines[2]["amount"] = 78.32
    doc, _ = await _process(session, tmp_path, monkeypatch, _body(lines))
    rows = _rows(session, doc)
    assert rows[2].review_status == "needs_check"
    return doc, rows


@pytest.mark.asyncio
async def test_reprocessing_replaces_the_check_and_clears_flags_it_no_longer_needs(session, tmp_path, monkeypatch):
    doc, rows = await _first_run_with_a_held_line(session, tmp_path, monkeypatch)
    fixed = await _extract(tmp_path, _body(good_lines()))        # a better reading: now it adds up
    apply_statement_check(session, doc.id, rows, fixed)
    session.commit()
    session.expire_all()
    rows = _rows(session, doc)
    assert all(r.review_status is None and r.review_reason is None for r in rows)
    (check,) = _check_row(session, doc)
    assert check.status == "ok"


@pytest.mark.asyncio
async def test_reprocessing_never_overwrites_a_line_a_person_already_decided(session, tmp_path, monkeypatch):
    doc, rows = await _first_run_with_a_held_line(session, tmp_path, monkeypatch)
    held = rows[2]
    held.review_status, held.checked_by = "confirmed", "pedro"
    held.review_reason = "Held out of the totals. (the original reason)"
    held.source_page = 7
    session.add(held)
    session.commit()
    # the same doubtful reading again, and a different line also flagged
    again = good_lines()
    again[2]["amount"] = 78.32
    again[0]["amount"] = 40.00
    apply_statement_check(session, doc.id, rows, await _extract(tmp_path, _body(again)))
    session.commit()
    session.expire_all()
    rows = _rows(session, doc)
    assert (rows[2].review_status, rows[2].checked_by, rows[2].source_page) == ("confirmed", "pedro", 7)
    assert rows[2].review_reason == "Held out of the totals. (the original reason)"
    assert rows[0].review_status == "needs_check"          # an undecided line is still flagged normally
    assert len(_check_row(session, doc)) == 1


@pytest.mark.asyncio
async def test_reprocessing_leaves_a_rejected_line_and_a_line_with_a_checker_alone(session, tmp_path, monkeypatch):
    doc, rows = await _first_run_with_a_held_line(session, tmp_path, monkeypatch)
    rows[2].review_status = "rejected"
    rows[0].checked_by = "ana"                              # decided by name though the status is still unset
    session.add(rows[2]); session.add(rows[0])
    session.commit()
    ok = await _extract(tmp_path, _body(good_lines()))
    apply_statement_check(session, doc.id, rows, ok)
    session.commit()
    session.expire_all()
    rows = _rows(session, doc)
    assert rows[2].review_status == "rejected"
    assert rows[0].checked_by == "ana" and rows[0].source_page == 1


@pytest.mark.asyncio
async def test_a_difference_a_person_accepted_stays_accepted_when_it_is_the_same_difference(session, tmp_path, monkeypatch):
    doc, rows = await _first_run_with_a_held_line(session, tmp_path, monkeypatch)
    (check,) = _check_row(session, doc)
    check.status, check.resolved_by = "accepted", "pedro"
    session.add(check)
    session.commit()
    lines = good_lines()
    lines[2]["amount"] = 78.32
    apply_statement_check(session, doc.id, rows, await _extract(tmp_path, _body(lines)))
    session.commit()
    session.expire_all()
    (check,) = _check_row(session, doc)
    assert (check.status, check.resolved_by) == ("accepted", "pedro")
    lines[2]["amount"] = 70.00                               # a different difference reopens it
    apply_statement_check(session, doc.id, rows, await _extract(tmp_path, _body(lines)))
    session.commit()
    session.expire_all()
    (check,) = _check_row(session, doc)
    assert check.status == "mismatch" and check.resolved_by is None


@pytest.mark.asyncio
async def test_a_line_with_a_checker_is_decided_even_if_its_status_is_unset_or_still_needs_check(session, tmp_path, monkeypatch):
    doc, rows = await _first_run_with_a_held_line(session, tmp_path, monkeypatch)
    rows[0].checked_by = "ana"                     # status unset, but someone has looked at it
    rows[2].checked_by = "ana"                     # status still needs_check, someone has looked at it
    held_reason = rows[2].review_reason
    session.add(rows[0])
    session.add(rows[2])
    session.commit()
    worse = good_lines()
    worse[2]["amount"] = 78.32
    worse[0]["amount"] = 40.00                     # would flag line 0 ...
    apply_statement_check(session, doc.id, rows, await _extract(tmp_path, _body(worse)))
    session.commit()
    session.expire_all()
    rows = _rows(session, doc)
    assert rows[0].review_status is None           # ... but a person has decided it
    ok = await _extract(tmp_path, _body(good_lines()))      # ... and now the statement adds up
    apply_statement_check(session, doc.id, rows, ok)
    session.commit()
    session.expire_all()
    rows = _rows(session, doc)
    assert rows[2].review_status == "needs_check" and rows[2].review_reason == held_reason
