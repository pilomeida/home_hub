"""Storing statement positions and loan histories idempotently. All data is
synthetic (invented numbers, same shapes as the real documents)."""

import json
from datetime import date

import pytest
from sqlmodel import select

from app.domains.financials import handler as pipeline
from app.domains.financials.handler import FinancialsHandler
from app.models.debt import Debt, DebtDirection, DebtKind
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.position import (
    BalanceSnapshot, LoanMovement, LoanSnapshot, PositionExtraction, SavingsSnapshot,
)
from app.models.transaction import Transaction
from app.services import position_extraction as pe
from app.services.position_extraction import (
    ComponentRow, ExtractedBalance, ExtractedFund, ExtractedLoan, ExtractedPositions, Instalment,
)
from app.services.position_store import (
    apply_loan_history, apply_positions, assign_history_to_loan, create_loan_from_assignment,
    match_loan_for_history, process_loan_history_document, process_positions_document,
)
from tests.fakes.fake_gateway import FakeGateway


def _doc(session, tmp_path, name="d.pdf", category="statement_positions", h=None):
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4 fake")
    d = Document(filename=name, file_path=str(path), content_hash=h or name, source=DocumentSource.MANUAL,
                 status=DocumentStatus.PENDING, category=category)
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def _reply(text):
    return {"text": text, "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}


def _row(d, n, comp, amount, bal=None):
    return ComponentRow(date=d, instalment_number=n, component=comp, amount=amount, balance_after=bal)


def _loan(as_of_remaining=703.60, number="900100200", rows=None, **kw):
    rows = rows if rows is not None else [
        _row(date(2026, 7, 2), 37, "capital", 241.10, 758.90),
        _row(date(2026, 7, 2), 37, "capital", 55.30, 703.60),
        _row(date(2026, 7, 2), 37, "interest", 79.45),
    ]
    base = dict(number=number, label="CREDITO HABITACAO", rate_percent=2.5, term_months=300,
                capital_granted=5000.0, start_date=date(2024, 1, 15), capital_remaining=as_of_remaining,
                opening_balance=1000.0, rows=rows, next_due_date=date(2026, 8, 2), next_instalment_number=38,
                next_instalment=400.0, next_capital=300.0, next_interest=100.0)
    base.update(kw)
    return ExtractedLoan(**base)


def _positions(as_of=date(2026, 7, 31), loans=None, funds=None, balances=None):
    return ExtractedPositions(
        as_of=as_of,
        loans=[_loan()] if loans is None else loans,
        funds=[ExtractedFund("555", "A. Person", "FUND X", 10.5, 900.0, 1205.40, 50.0, date(2026, 8, 5))]
        if funds is None else funds,
        balances=[ExtractedBalance("deposit", "DEPOSITOS", 123.45)] if balances is None else balances,
    )


def _counts(session):
    return tuple(len(session.exec(select(m)).all()) for m in
                 (Debt, LoanMovement, LoanSnapshot, SavingsSnapshot, BalanceSnapshot))


# (a)
def test_apply_positions_creates_everything(session, tmp_path):
    doc = _doc(session, tmp_path)
    counts = apply_positions(session, doc, _positions())
    assert counts == {"loans": 1, "movements": 1, "snapshots": 1, "funds": 1, "balances": 1, "conflicts": 0}
    debt = session.exec(select(Debt)).one()
    assert debt.external_number == "900100200" and debt.loan_type == "mortgage"
    assert debt.kind == DebtKind.FORMAL and debt.direction == DebtDirection.OWED_BY_US
    assert debt.name == "CREDITO HABITACAO …0200" and debt.interest_rate == 2.5
    assert debt.original_amount == 5000.0 and float(debt.current_balance) == 703.60
    mv = session.exec(select(LoanMovement)).one()
    assert (mv.instalment_number, mv.capital, mv.interest) == (37, 296.40, 79.45)
    assert mv.balance_after == 703.60 and mv.document_id == doc.id
    snap = session.exec(select(LoanSnapshot)).one()
    assert snap.capital_remaining == 703.60 and snap.next_due_date == date(2026, 8, 2)
    assert session.exec(select(SavingsSnapshot)).one().value == 1205.40
    assert session.exec(select(BalanceSnapshot)).one().amount == 123.45


# (b)
def test_apply_positions_twice_inserts_nothing(session, tmp_path):
    doc = _doc(session, tmp_path)
    apply_positions(session, doc, _positions())
    before = _counts(session)
    again = apply_positions(session, doc, _positions())
    assert again == {"loans": 0, "movements": 0, "snapshots": 0, "funds": 0, "balances": 0, "conflicts": 0}
    assert _counts(session) == before


# (c)
def test_older_as_of_does_not_roll_back_balance(session, tmp_path):
    doc = _doc(session, tmp_path)
    apply_positions(session, doc, _positions(as_of=date(2026, 7, 31)))
    older = _loan(as_of_remaining=900.0, rows=[], opening_balance=None, rate_percent=3.0)
    apply_positions(session, _doc(session, tmp_path, "o.pdf"), _positions(as_of=date(2026, 5, 31), loans=[older]))
    debt = session.exec(select(Debt)).one()
    assert float(debt.current_balance) == 703.60 and debt.interest_rate == 2.5
    assert len(session.exec(select(LoanSnapshot)).all()) == 2


# (d)
def test_zero_remaining_closes_loan(session, tmp_path):
    loan = _loan(as_of_remaining=0.0, rows=[], opening_balance=None, next_due_date=None, next_instalment=None,
                 next_capital=None, next_interest=None)
    apply_positions(session, _doc(session, tmp_path), _positions(loans=[loan]))
    assert session.exec(select(Debt)).one().status == "closed"


_STATEMENT_JSON = {
    "as_of": "2026-07-31",
    "loans": [{
        "number": "900100200", "label": "CREDITO HABITACAO", "rate_percent": 2.5, "term_months": 300,
        "capital_granted": 5000.0, "start_date": "2024-01-15", "capital_remaining": 703.60,
        "opening_balance": 1000.0,
        "rows": [
            {"date": "2026-07-02", "instalment_number": 37, "component": "capital", "amount": 296.40, "balance_after": 703.60},
            {"date": "2026-07-02", "instalment_number": 37, "component": "interest", "amount": 79.45, "balance_after": None},
        ],
    }],
    "funds": [], "balances": [],
}


# (e)
@pytest.mark.asyncio
async def test_failed_reconcile_stores_failed_and_needs_attention(session, tmp_path):
    bad = json.loads(json.dumps(_STATEMENT_JSON))
    bad["loans"][0]["opening_balance"] = 5000.0  # does not reconcile
    doc = _doc(session, tmp_path)
    ext = await process_positions_document(session, doc, gateway=FakeGateway([_reply(json.dumps(bad))]))
    assert ext.status == "failed" and ext.error
    assert doc.status == DocumentStatus.NEEDS_ATTENTION
    assert session.exec(select(Debt)).all() == []


# (f)
@pytest.mark.asyncio
async def test_ok_extraction_is_idempotent_no_second_call(session, tmp_path):
    doc = _doc(session, tmp_path)
    fake = FakeGateway([_reply(json.dumps(_STATEMENT_JSON))])
    first = await process_positions_document(session, doc, gateway=fake)
    assert first.status == "ok"
    second = await process_positions_document(session, doc, gateway=fake)
    assert second.id == first.id and len(fake.requests) == 1


# (g)
@pytest.mark.asyncio
async def test_positions_document_creates_no_transactions(session, tmp_path, monkeypatch):
    fake = FakeGateway([_reply(json.dumps(_STATEMENT_JSON))])
    monkeypatch.setattr(pe, "get_gateway", lambda: fake)

    async def no_wiki(session, document, context=None, **kw):
        return None
    monkeypatch.setattr(pipeline, "ingest_into_wiki", no_wiki)
    doc = _doc(session, tmp_path)
    result = await pipeline.process_financials_document(session, doc)
    assert result.status == DocumentStatus.PROCESSED
    assert session.exec(select(Transaction)).all() == []
    assert len(session.exec(select(LoanMovement)).all()) == 1


def _hist_rows(numbers, capital=100.0, interest=20.0, start=1000.0, first=35):
    rows = []
    for n in numbers:
        bal = start - capital * (n - first + 1)
        d = date(2026, 1, 1 + n - first)
        rows.append({"date": d.isoformat(), "instalment_number": n, "component": "capital",
                     "amount": capital, "balance_after": bal})
        rows.append({"date": d.isoformat(), "instalment_number": n, "component": "interest",
                     "amount": interest, "balance_after": None})
    return json.dumps({"rows": rows})


async def _history(session, tmp_path, name, numbers):
    doc = _doc(session, tmp_path, name, category="loan_history")
    return doc, await process_loan_history_document(session, doc, gateway=FakeGateway([_reply(_hist_rows(numbers))]))


# (i) first, since (h) builds on it
@pytest.mark.asyncio
async def test_unmatched_history_needs_loan_then_assignment(session, tmp_path):
    doc, ext = await _history(session, tmp_path, "h1.pdf", [35, 36, 37])
    assert ext.status == "needs_loan" and ext.payload_json
    assert doc.status != DocumentStatus.NEEDS_ATTENTION
    debt = create_loan_from_assignment(session, "9001-0020-1", "Third loan", "personal")
    assert debt.external_number == "900100201" and debt.kind == DebtKind.FORMAL
    assert debt.direction == DebtDirection.OWED_BY_US and debt.interest_rate is None
    counts = assign_history_to_loan(session, ext, debt)
    assert counts["movements"] == 3
    session.refresh(ext)
    session.refresh(debt)
    assert ext.status == "ok"
    assert len(session.exec(select(LoanMovement).where(LoanMovement.debt_id == debt.id)).all()) == 3
    # newest instalment 37: capital 100, interest 20, balance after = 1000 - 300 = 700
    assert float(debt.current_balance) == 700.0
    assert debt.interest_rate == round(20 * 12 / (700 + 100) * 100, 3)
    assert debt.original_amount == 1000.0


# (h)
@pytest.mark.asyncio
async def test_matching_history_applies_and_duplicate_changes_nothing(session, tmp_path):
    _, ext = await _history(session, tmp_path, "h1.pdf", [35, 36, 37])
    debt = create_loan_from_assignment(session, "900100201", "L", "personal")
    assign_history_to_loan(session, ext, debt)
    doc2, ext2 = await _history(session, tmp_path, "h2.pdf", [36, 37, 38])
    assert ext2.status == "ok"
    movements = session.exec(select(LoanMovement).where(LoanMovement.debt_id == debt.id)).all()
    assert len(movements) == 4
    before = _counts(session)
    doc3, ext3 = await _history(session, tmp_path, "h3.pdf", [36, 37, 38])
    assert ext3.status == "ok" and _counts(session) == before


@pytest.mark.asyncio
async def test_single_overlap_is_not_a_match(session, tmp_path):
    _, ext = await _history(session, tmp_path, "h1.pdf", [35, 36, 37])
    debt = create_loan_from_assignment(session, "900100201", "L", "personal")
    assign_history_to_loan(session, ext, debt)
    instalments = pe.aggregate_components([_row(date(2026, 3, 1), 37, "capital", 100.0, 700.0),
                                           _row(date(2026, 3, 1), 37, "interest", 20.0)])
    instalments.append(Instalment(40, date(2026, 6, 1), 100.0, 20.0, 0.0, 400.0))
    assert match_loan_for_history(session, instalments) is None


# (j)
def test_conflicting_duplicate_is_kept_and_reported(session, tmp_path):
    debt = create_loan_from_assignment(session, "900100201", "L", "personal")
    doc = _doc(session, tmp_path, category="loan_history")
    apply_loan_history(session, doc, debt, [Instalment(37, date(2026, 3, 1), 100.0, 20.0, 0.0, 700.0)])
    counts = apply_loan_history(session, doc, debt, [Instalment(37, date(2026, 3, 1), 150.0, 20.0, 0.0, 650.0)])
    assert counts["conflicts"] == 1 and counts["movements"] == 0
    assert session.exec(select(LoanMovement)).one().capital == 100.0


# (k)
def test_printout_and_statement_merge_into_one_row(session, tmp_path):
    loan = _loan()
    debt = create_loan_from_assignment(session, "900100200", "L", "mortgage")
    doc = _doc(session, tmp_path, category="loan_history")
    apply_loan_history(session, doc, debt, [Instalment(37, date(2026, 7, 2), 296.40, 79.45, 0.0, None)])
    rows = loan.rows + [_row(date(2026, 7, 2), 37, "insurance_life", 5.5)]
    stmt = _doc(session, tmp_path, "s.pdf")
    counts = apply_positions(session, stmt, _positions(loans=[_loan(rows=rows)], funds=[], balances=[]))
    assert counts["movements"] == 0 and counts["conflicts"] == 0 and counts["loans"] == 0
    mv = session.exec(select(LoanMovement)).one()
    assert mv.insurance == 5.5 and mv.balance_after == 703.60 and mv.document_id == doc.id


# withdraw: Block B - registered data is never discarded, only detached
@pytest.mark.asyncio
async def test_withdraw_detaches_rows_and_keeps_them(session, tmp_path):
    doc = _doc(session, tmp_path)
    apply_positions(session, doc, _positions())
    session.add(PositionExtraction(document_id=doc.id, status="ok"))
    session.commit()
    before = _counts(session)
    await FinancialsHandler().withdraw(session, doc)
    session.expire_all()
    assert _counts(session) == before  # debts, movements, snapshots, funds, balances all stay
    for model in (LoanMovement, LoanSnapshot, SavingsSnapshot, BalanceSnapshot):
        assert [r.document_id for r in session.exec(select(model)).all()] == [None]
    assert session.exec(select(PositionExtraction)).all() == []


def test_detach_positions_helper_touches_only_that_document(session, tmp_path):
    from app.models.position import LoanAlert
    from app.services.position_store import detach_positions
    one, two = _doc(session, tmp_path, "one.pdf"), _doc(session, tmp_path, "two.pdf")
    apply_positions(session, one, _positions())
    apply_positions(session, two, _positions(as_of=date(2026, 8, 31), loans=[_loan(rows=[])], funds=[], balances=[]))
    debt = session.exec(select(Debt)).one()
    session.add(LoanAlert(debt_id=debt.id, kind="interest_only", ref="inst:1", message="m",
                          detected_on=date(2026, 7, 31), document_id=one.id))
    session.commit()
    detach_positions(session, one.id)
    session.commit()
    session.expire_all()
    assert session.exec(select(LoanMovement)).one().document_id is None
    assert sorted(s.document_id or 0 for s in session.exec(select(LoanSnapshot)).all()) == [0, two.id]
    assert session.exec(select(LoanAlert)).one().document_id is None


def test_new_categories_registered_but_not_offered_to_the_classifier():
    from app.domains.registry import get_spec
    from app.models.domain import Domain
    from app.services.domain_classifier import build_system_prompt
    spec = get_spec(Domain.FINANCIALS)
    assert {"statement_positions", "loan_history"} <= {c.value for c in spec.categories}
    prompt = build_system_prompt([spec])
    assert "statement_positions" not in prompt and "loan_history" not in prompt
    assert 'category "statement"' in prompt


# ---- fix round 1 -------------------------------------------------------

def _stmt_json(as_of, remaining, rows, opening=None, rate=2.5, number="900100201"):
    return json.dumps({"as_of": as_of, "loans": [{
        "number": number, "label": "CREDITO HABITACAO", "rate_percent": rate, "term_months": 300,
        "capital_granted": 5000.0, "start_date": "2024-01-15", "capital_remaining": remaining,
        "opening_balance": opening, "rows": rows}], "funds": [], "balances": []})


_ROWS_36_37 = [
    {"date": "2026-01-02", "instalment_number": 36, "component": "capital", "amount": 100.0, "balance_after": 800.0},
    {"date": "2026-01-02", "instalment_number": 36, "component": "interest", "amount": 20.0, "balance_after": None},
    {"date": "2026-01-03", "instalment_number": 37, "component": "capital", "amount": 100.0, "balance_after": 700.0},
    {"date": "2026-01-03", "instalment_number": 37, "component": "interest", "amount": 20.0, "balance_after": None},
]


async def _statement(session, tmp_path, name, as_of, remaining, rows, **kw):
    doc = _doc(session, tmp_path, name)
    ext = await process_positions_document(session, doc, gateway=FakeGateway([_reply(_stmt_json(as_of, remaining, rows, **kw))]))
    assert ext.status == "ok", ext.error
    return doc


def _debt(session):
    session.expire_all()
    return session.exec(select(Debt)).one()


def _movs(session):
    session.expire_all()
    return {m.instalment_number: m for m in session.exec(select(LoanMovement)).all()}


# Block B: withdrawing the statement or the printout leaves every row in place
@pytest.mark.asyncio
async def test_withdraw_statement_keeps_overlapping_rows_and_balance(session, tmp_path):
    stmt = await _statement(session, tmp_path, "s.pdf", "2026-02-28", 650.0, _ROWS_36_37, opening=900.0)
    pdoc, ext = await _history(session, tmp_path, "p.pdf", [35, 36, 37])
    assert ext.status == "ok" and float(_debt(session).current_balance) == 650.0
    await FinancialsHandler().withdraw(session, stmt)
    movs = _movs(session)
    assert sorted(movs) == [35, 36, 37]
    assert movs[35].document_id == pdoc.id
    assert movs[36].document_id is None and movs[37].document_id is None  # the statement's rows, detached
    assert float(_debt(session).current_balance) == 650.0
    assert len(session.exec(select(LoanSnapshot)).all()) == 1  # the statement snapshot stays, detached


@pytest.mark.asyncio
async def test_withdraw_printout_keeps_every_row(session, tmp_path):
    stmt = await _statement(session, tmp_path, "s.pdf", "2026-02-28", 650.0, _ROWS_36_37, opening=900.0)
    pdoc, _ = await _history(session, tmp_path, "p.pdf", [35, 36, 37])
    await FinancialsHandler().withdraw(session, pdoc)
    movs = _movs(session)
    assert sorted(movs) == [35, 36, 37]
    assert movs[35].document_id is None and movs[36].document_id == stmt.id
    assert float(_debt(session).current_balance) == 650.0
    assert session.exec(select(PositionExtraction).where(PositionExtraction.document_id == pdoc.id)).first() is None
    assert session.exec(select(PositionExtraction).where(PositionExtraction.document_id == stmt.id)).first() is not None


@pytest.mark.asyncio
async def test_withdrawing_the_only_document_leaves_the_loan_intact(session, tmp_path):
    stmt = await _statement(session, tmp_path, "s.pdf", "2026-02-28", 650.0, _ROWS_36_37, opening=900.0)
    await FinancialsHandler().withdraw(session, stmt)
    debt = _debt(session)
    assert float(debt.current_balance) == 650.0 and debt.status == "active" and debt.interest_rate == 2.5
    movs = _movs(session)
    assert sorted(movs) == [36, 37] and all(m.document_id is None for m in movs.values())
    snap = session.exec(select(LoanSnapshot)).one()
    assert snap.document_id is None and snap.capital_remaining == 650.0
    session.expire_all()
    assert session.get(Document, stmt.id) is not None


@pytest.mark.asyncio
async def test_reupload_after_withdraw_reattaches_without_duplicating(session, tmp_path):
    stmt = await _statement(session, tmp_path, "s.pdf", "2026-02-28", 650.0, _ROWS_36_37, opening=900.0)
    await FinancialsHandler().withdraw(session, stmt)
    before = _counts(session)
    again = await process_positions_document(
        session, stmt, gateway=FakeGateway([_reply(_stmt_json("2026-02-28", 650.0, _ROWS_36_37, opening=900.0))]))
    assert again.status == "ok"
    assert _counts(session) == before
    movs = _movs(session)
    assert all(m.document_id == stmt.id for m in movs.values())
    assert session.exec(select(LoanSnapshot)).one().document_id == stmt.id


def test_reupload_reattaches_funds_balances_and_history(session, tmp_path):
    doc = _doc(session, tmp_path)
    apply_positions(session, doc, _positions())
    from app.services.position_store import detach_positions
    detach_positions(session, doc.id)
    session.commit()
    new = _doc(session, tmp_path, "again.pdf")
    counts = apply_positions(session, new, _positions())
    assert counts == {"loans": 0, "movements": 0, "snapshots": 0, "funds": 0, "balances": 0, "conflicts": 0}
    for model in (LoanMovement, LoanSnapshot, SavingsSnapshot, BalanceSnapshot):
        assert [r.document_id for r in session.exec(select(model)).all()] == [new.id]


def test_reattach_never_steals_rows_of_another_document(session, tmp_path):
    first, second = _doc(session, tmp_path, "a.pdf"), _doc(session, tmp_path, "b.pdf")
    apply_positions(session, first, _positions())
    apply_positions(session, second, _positions())
    assert session.exec(select(LoanMovement)).one().document_id == first.id


def test_reattach_disagreeing_instalment_stays_detached(session, tmp_path):
    from app.services.position_store import detach_positions
    debt = create_loan_from_assignment(session, "900100200", "L", "mortgage")
    doc = _doc(session, tmp_path, category="loan_history")
    apply_loan_history(session, doc, debt, [Instalment(37, date(2026, 7, 2), 100.0, 20.0, 0.0, 700.0)])
    detach_positions(session, doc.id)
    session.commit()
    other = _doc(session, tmp_path, "o.pdf", category="loan_history")
    counts = apply_loan_history(session, other, debt, [Instalment(37, date(2026, 7, 2), 150.0, 20.0, 0.0, 650.0)])
    assert counts["conflicts"] == 1
    assert session.exec(select(LoanMovement)).one().document_id is None


# I2: interest-only history never sets a balance; then amortising ones do
def test_interest_only_history_leaves_balance_unknown(session, tmp_path):
    debt = create_loan_from_assignment(session, "900100201", "L", "personal")
    doc = _doc(session, tmp_path, category="loan_history")
    only = [Instalment(1, date(2026, 1, 1), 0.0, 20.0, 0.0, None), Instalment(2, date(2026, 2, 1), 0.0, 20.0, 0.0, None)]
    counts = apply_loan_history(session, doc, debt, only)
    assert counts["movements"] == 2
    session.refresh(debt)
    assert float(debt.current_balance) == 0.0 and debt.interest_rate is None and debt.original_amount == 0
    amort = [Instalment(3, date(2026, 3, 1), 100.0, 20.0, 0.0, 900.0)]
    apply_loan_history(session, doc, debt, amort)
    session.refresh(debt)
    assert float(debt.current_balance) == 900.0
    assert debt.interest_rate == round(20 * 12 / (900 + 100) * 100, 3)
    assert debt.original_amount == 1000.0  # m4: back-filled by a later apply


# m1
@pytest.mark.asyncio
async def test_disagreement_blocks_both_the_match_and_the_assignment(session, tmp_path):
    debt = create_loan_from_assignment(session, "900100201", "L", "personal")
    base = _doc(session, tmp_path, "b.pdf", category="loan_history")
    apply_loan_history(session, base, debt, [Instalment(36, date(2026, 1, 2), 150.0, 20.0, 0.0, 800.0)])
    doc, ext = await _history(session, tmp_path, "p.pdf", [35, 36, 37])
    assert ext.status == "needs_loan"  # a disagreement blocks the match
    with pytest.raises(ValueError):  # ... and the manual assignment (I4)
        assign_history_to_loan(session, ext, debt)


# m2
def test_later_statement_backfills_and_reopens(session, tmp_path):
    debt = create_loan_from_assignment(session, "900100200", "", "mortgage")
    debt.name = None
    session.add(debt)
    session.commit()
    closed = _loan(as_of_remaining=0.0, rows=[], opening_balance=None, next_due_date=None, next_instalment=None,
                   next_capital=None, next_interest=None)
    apply_positions(session, _doc(session, tmp_path, "a.pdf"), _positions(as_of=date(2026, 5, 31), loans=[closed], funds=[], balances=[]))
    debt = _debt(session)
    assert debt.status == "closed" and debt.name == "CREDITO HABITACAO …0200"
    assert debt.capital_granted == 5000.0 and debt.term_months == 300 and debt.start_date == date(2024, 1, 15)
    debt.name = "Mine"
    session.add(debt)
    session.commit()
    apply_positions(session, _doc(session, tmp_path, "b.pdf"), _positions(as_of=date(2026, 7, 31), funds=[], balances=[]))
    debt = _debt(session)
    assert debt.status == "active" and debt.name == "Mine"


# m3: an older printout never rolls the balance back
def test_older_printout_leaves_balance(session, tmp_path):
    apply_positions(session, _doc(session, tmp_path), _positions())
    debt = session.exec(select(Debt)).one()
    old = [Instalment(20, date(2025, 1, 2), 100.0, 20.0, 0.0, 1500.0), Instalment(21, date(2025, 2, 2), 100.0, 20.0, 0.0, 1400.0)]
    apply_loan_history(session, _doc(session, tmp_path, "o.pdf", category="loan_history"), debt, old)
    session.refresh(debt)
    assert float(debt.current_balance) == 703.60 and debt.interest_rate == 2.5


# m3: two loans matching -> needs_loan
@pytest.mark.asyncio
async def test_two_matching_loans_need_assignment(session, tmp_path):
    insts = [Instalment(35, date(2026, 1, 1), 100.0, 20.0, 0.0, 900.0), Instalment(36, date(2026, 1, 2), 100.0, 20.0, 0.0, 800.0)]
    for number in ("900100201", "900100202"):
        d = create_loan_from_assignment(session, number, "L", "personal")
        apply_loan_history(session, _doc(session, tmp_path, f"b{number}.pdf", category="loan_history"), d, insts)
    _, ext = await _history(session, tmp_path, "p.pdf", [35, 36, 37])
    assert ext.status == "needs_loan"


# m3: a disagreement blocks a match
def test_disagreement_blocks_match(session, tmp_path):
    d = create_loan_from_assignment(session, "900100201", "L", "personal")
    base = [Instalment(n, date(2026, 1, n), 100.0, 20.0, 0.0, 1000.0 - 100 * n) for n in (1, 2, 3)]
    apply_loan_history(session, _doc(session, tmp_path, category="loan_history"), d, base)
    assert match_loan_for_history(session, base) is d or match_loan_for_history(session, base).id == d.id
    changed = base[:2] + [Instalment(3, date(2026, 1, 3), 140.0, 20.0, 0.0, 660.0)]
    assert match_loan_for_history(session, changed) is None


# m3: a mid-apply failure leaves no partial rows
@pytest.mark.asyncio
async def test_mid_apply_failure_leaves_nothing(session, tmp_path, monkeypatch):
    from app.services import position_store

    def boom(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(position_store, "_upsert_movement", boom)
    doc = _doc(session, tmp_path)
    ext = await process_positions_document(
        session, doc, gateway=FakeGateway([_reply(_stmt_json("2026-02-28", 700.0, _ROWS_36_37, opening=900.0))]))
    assert ext.status == "failed" and "boom" in ext.error
    assert doc.status == DocumentStatus.NEEDS_ATTENTION
    assert _counts(session) == (0, 0, 0, 0, 0)


# m3: strengthened (i) through the real route
@pytest.mark.asyncio
async def test_unmatched_history_through_pipeline_ends_processed(session, tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "get_gateway", lambda: FakeGateway([_reply(_hist_rows([35, 36, 37]))]))

    async def no_wiki(session, document, context=None, **kw):
        return None
    monkeypatch.setattr(pipeline, "ingest_into_wiki", no_wiki)
    doc = _doc(session, tmp_path, "h.pdf", category="loan_history")
    result = await pipeline.process_financials_document(session, doc)
    assert result.status == DocumentStatus.PROCESSED
    assert session.exec(select(PositionExtraction)).one().status == "needs_loan"


# --- I2: the revised rate is what gets stored ------------------------------


def test_apply_positions_stores_next_rate_and_uses_it_for_the_debt(session, tmp_path):
    doc = _doc(session, tmp_path)
    apply_positions(session, doc, _positions(loans=[_loan(rate_percent=2.0, next_rate_percent=3.5)]))
    snap = session.exec(select(LoanSnapshot)).one()
    assert snap.rate_percent == 2.0 and snap.next_rate_percent == 3.5
    assert session.exec(select(Debt)).one().interest_rate == 3.5


def test_apply_positions_debt_rate_falls_back_to_applied_then_derived(session, tmp_path):
    doc = _doc(session, tmp_path)
    apply_positions(session, doc, _positions(loans=[_loan(rate_percent=2.0)]))
    assert session.exec(select(Debt)).one().interest_rate == 2.0
    doc2 = _doc(session, tmp_path, name="e.pdf")
    apply_positions(session, doc2, _positions(
        as_of=date(2026, 8, 31),
        loans=[_loan(rate_percent=None, next_interest=200.0, next_capital=200.0, next_instalment=400.0,
                     as_of_remaining=48000.0, rows=[])]))
    assert session.exec(select(Debt)).one().interest_rate == 5.0


# --- I4: assignment is refused when overlapping instalments disagree -------


@pytest.mark.asyncio
async def test_assign_history_rejects_disagreeing_overlap_and_changes_nothing(session, tmp_path):
    _, first = await _history(session, tmp_path, "a.pdf", [35, 36, 37])
    debt = create_loan_from_assignment(session, "900100201", "A", "personal")
    assign_history_to_loan(session, first, debt)
    _, other = await _history(session, tmp_path, "b.pdf", [37, 38])
    # same instalment number 37, different amounts (another tranche)
    other.payload_json = other.payload_json.replace('"capital": 100.0', '"capital": 140.0')
    session.add(other)
    session.commit()
    before = len(session.exec(select(LoanMovement)).all())
    with pytest.raises(ValueError, match="instalment 37"):
        assign_history_to_loan(session, other, debt)
    session.refresh(other)
    assert other.status == "needs_loan" and len(session.exec(select(LoanMovement)).all()) == before


@pytest.mark.asyncio
async def test_assign_history_allows_non_overlapping_instalments(session, tmp_path):
    _, first = await _history(session, tmp_path, "a.pdf", [35, 36])
    debt = create_loan_from_assignment(session, "900100201", "A", "personal")
    assign_history_to_loan(session, first, debt)
    _, other = await _history(session, tmp_path, "b.pdf", [37, 38])
    assign_history_to_loan(session, other, debt)
    assert len(session.exec(select(LoanMovement)).all()) == 4


# --- M1: loan numbers -------------------------------------------------------


@pytest.mark.parametrize("number", ["1234567", "", "0"])
def test_apply_positions_rejects_numbers_under_eight_digits(session, tmp_path, number):
    with pytest.raises(ValueError, match="fewer than 8 digits"):
        apply_positions(session, _doc(session, tmp_path), _positions(loans=[_loan(number=number)]))
    assert session.exec(select(Debt)).all() == []


@pytest.mark.parametrize("number", ["9001002001", "10020"[:0] + "900100200" + "7"])
def test_apply_positions_rejects_number_containing_an_existing_loan(session, tmp_path, number):
    apply_positions(session, _doc(session, tmp_path), _positions())  # loan 900100200
    with pytest.raises(ValueError, match="overlaps"):
        apply_positions(session, _doc(session, tmp_path, "b.pdf"), _positions(loans=[_loan(number=number)]))
    assert len(session.exec(select(Debt)).all()) == 1


def test_apply_positions_rejects_number_contained_in_an_existing_loan(session, tmp_path):
    create_loan_from_assignment(session, "99900100200999", "Big", "personal")
    with pytest.raises(ValueError, match="overlaps"):
        apply_positions(session, _doc(session, tmp_path), _positions())  # 900100200 is inside


@pytest.mark.asyncio
async def test_short_loan_number_fails_the_extraction_with_a_reason(session, tmp_path):
    stmt = json.loads(json.dumps(_STATEMENT_JSON))
    stmt["loans"][0]["number"] = "12-34"
    doc = _doc(session, tmp_path)
    ext = await process_positions_document(session, doc, gateway=FakeGateway([_reply(json.dumps(stmt))]))
    assert ext.status == "failed" and "fewer than 8 digits" in ext.error
    assert doc.status == DocumentStatus.NEEDS_ATTENTION


# --- M2: a loan paid down to zero is closed --------------------------------


def test_history_ending_at_zero_balance_closes_the_loan(session, tmp_path):
    debt = create_loan_from_assignment(session, "900100201", "L", "personal")
    doc = _doc(session, tmp_path, "z.pdf", category="loan_history")
    apply_loan_history(session, doc, debt, [
        Instalment(11, date(2026, 8, 2), 100.0, 5.0, 0.0, 100.0),
        Instalment(12, date(2026, 9, 2), 100.0, 3.0, 0.0, 0.0),
    ])
    session.refresh(debt)
    assert float(debt.current_balance) == 0.0 and debt.status == "closed"



# --- Block C: insurance split ---------------------------------------------

def test_apply_history_stores_both_insurance_parts(session, tmp_path):
    debt = create_loan_from_assignment(session, "900100200", "L", "mortgage")
    doc = _doc(session, tmp_path, category="loan_history")
    apply_loan_history(session, doc, debt, [
        Instalment(37, date(2026, 7, 2), 296.4, 79.45, 6.75, 703.6, insurance_life=5.5, insurance_building=1.25)])
    mv = session.exec(select(LoanMovement)).one()
    assert (mv.insurance, mv.insurance_life, mv.insurance_building) == (6.75, 5.5, 1.25)


def test_upsert_fills_insurance_parts_only_when_empty_never_overwrites(session, tmp_path):
    debt = create_loan_from_assignment(session, "900100200", "L", "mortgage")
    doc = _doc(session, tmp_path, category="loan_history")
    apply_loan_history(session, doc, debt, [Instalment(37, date(2026, 7, 2), 296.4, 79.45, 0.0, None)])
    apply_loan_history(session, doc, debt, [
        Instalment(37, date(2026, 7, 2), 296.4, 79.45, 6.75, None, insurance_life=5.5, insurance_building=1.25)])
    mv = session.exec(select(LoanMovement)).one()
    assert (mv.insurance, mv.insurance_life, mv.insurance_building) == (6.75, 5.5, 1.25)
    apply_loan_history(session, doc, debt, [
        Instalment(37, date(2026, 7, 2), 296.4, 79.45, 99.0, None, insurance_life=88.0, insurance_building=11.0)])
    session.expire_all()
    mv = session.exec(select(LoanMovement)).one()
    assert (mv.insurance, mv.insurance_life, mv.insurance_building) == (6.75, 5.5, 1.25)


def test_statement_rows_carry_the_split_into_the_movement(session, tmp_path):
    doc = _doc(session, tmp_path)
    rows = _loan().rows + [_row(date(2026, 7, 2), 37, "insurance_life", 5.5),
                           _row(date(2026, 7, 2), 37, "insurance_building", 1.25)]
    apply_positions(session, doc, _positions(loans=[_loan(rows=rows)], funds=[], balances=[]))
    mv = session.exec(select(LoanMovement)).one()
    assert (mv.insurance, mv.insurance_life, mv.insurance_building) == (6.75, 5.5, 1.25)


def test_instalment_payload_round_trips_and_old_payloads_default_to_zero():
    from app.services.position_store import _instalments_from_list, _instalments_to_list
    inst = Instalment(1, date(2026, 1, 2), 1.0, 2.0, 4.0, 9.0, insurance_life=3.0, insurance_building=1.0)
    assert _instalments_from_list(_instalments_to_list([inst])) == [inst]
    old = {"number": 1, "date": "2026-01-02", "capital": 1.0, "interest": 2.0, "insurance": 4.0, "balance_after": 9.0}
    got = _instalments_from_list([old])[0]
    assert (got.insurance, got.insurance_life, got.insurance_building) == (4.0, 0.0, 0.0)
