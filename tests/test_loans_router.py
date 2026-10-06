"""Loans & Savings page. All data is synthetic, built through the production
store (apply_positions) and the link_debt route."""
from datetime import date
from decimal import Decimal

import pytest

from sqlmodel import select

from app.models.debt import Debt
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.position import SavingsSnapshot
from app.models.transaction import Category, Transaction, TransactionType
from app.services.loan_math import position_totals, savings_lines
from app.services.position_extraction import (
    ComponentRow, ExtractedBalance, ExtractedFund, ExtractedLoan, ExtractedPositions,
)
from app.services.position_store import apply_positions
from app.templating import templates

AS_OF = date(2026, 7, 31)


def _doc(session, n=1):
    d = Document(filename=f"p{n}.pdf", file_path=f"/tmp/p{n}.pdf", content_hash=f"loans-router-{n}",
                 source=DocumentSource.MANUAL, status=DocumentStatus.PENDING, category="statement_positions")
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def _row(d, n, comp, amount, bal=None):
    return ComponentRow(date=d, instalment_number=n, component=comp, amount=amount, balance_after=bal)


def _loan(**kw):
    rows = [
        _row(date(2026, 6, 2), 36, "capital", 300.0, 1000.0),
        _row(date(2026, 6, 2), 36, "interest", 80.0),
        _row(date(2026, 7, 2), 37, "capital", 250.0, 750.0),
        _row(date(2026, 7, 2), 37, "capital", 57.0, 693.0),
        _row(date(2026, 7, 2), 37, "interest", 79.0),
        _row(date(2026, 7, 2), 37, "insurance_life", 12.5),
    ]
    base = dict(number="900100200", label="CREDITO HABITACAO", rate_percent=3.125, term_months=300,
                capital_granted=5000.0, start_date=date(2024, 1, 15), capital_remaining=693.0,
                opening_balance=1300.0, rows=rows, next_due_date=date(2026, 8, 2), next_instalment_number=38,
                next_instalment=400.0, next_capital=300.0, next_interest=100.0)
    base.update(kw)
    return ExtractedLoan(**base)


def _positions(as_of=AS_OF, loans=None, funds=None, balances=None):
    return ExtractedPositions(
        as_of=as_of,
        loans=[_loan()] if loans is None else loans,
        funds=[ExtractedFund("555", "A. Person", "FUND X", 10.5, 900.0, 1100.0, 50.0, date(2026, 8, 5))]
        if funds is None else funds,
        balances=[ExtractedBalance("deposit", "DEPOSITOS", 200.0), ExtractedBalance("card", "CARTAO", 40.0)]
        if balances is None else balances,
    )


def _seed(session, **kw):
    apply_positions(session, _doc(session), _positions(**kw))
    return session.exec(select(Debt).where(Debt.external_number.isnot(None))).first()


def test_index_shows_loan_savings_and_totals(client, session):
    _seed(session)
    r = client.get("/financials/loans")
    assert r.status_code == 200
    html = r.text
    assert "CREDITO HABITACAO …0200" in html
    assert "3.125%" in html
    assert "€693" in html
    assert "2 Aug 2026" in html and "400.00" in html
    assert "12.50" in html and "insurance" in html
    assert "yrs" in html or "mo" in html          # payoff estimate
    assert "FUND X" in html and "A. Person" in html
    assert "1,100" in html and "+200" in html or "+€200" in html   # value and gain
    assert "DEPOSITOS" in html and "CARTAO" in html
    assert "Data as of 31 Jul 2026" in html
    assert "/financials/transactions?debt_id=" not in html  # no informal debts yet


def test_totals_identity_and_values(session):
    _seed(session)
    t = position_totals(session, date(2026, 8, 10))
    assert t.savings_total == 1100.0 and t.deposits_total == 200.0
    assert t.debt_total == 693.0 and t.cards_total == 40.0
    assert t.net_position == t.savings_total + t.deposits_total - t.debt_total - t.cards_total
    assert t.as_of == AS_OF and t.stale_days == 10


def test_totals_ignore_bank_loans_total_balance(session):
    _seed(session, balances=[ExtractedBalance("loans_total", "Total loans", 99999.0)])
    assert position_totals(session, date(2026, 8, 1)).debt_total == 693.0


def test_savings_lines_newest_snapshot_per_fund(session):
    apply_positions(session, _doc(session, 1), _positions(as_of=date(2026, 6, 30), loans=[]))
    apply_positions(session, _doc(session, 2),
                    _positions(loans=[], funds=[ExtractedFund("555", "A. Person", "FUND X", 10.5, 900.0, 1100.0, None, None)]))
    lines = savings_lines(session)
    assert len(lines) == 1
    assert lines[0].value == 1100.0 and lines[0].gain == 200.0 and lines[0].as_of == AS_OF


def test_stale_days_and_marker(session):
    _seed(session)
    t = position_totals(session, date(2026, 12, 31))
    assert t.stale_days == 153
    macros = templates.env.get_template("loans/_macros.html").module
    assert "loan-stale" in str(macros.freshness(t.as_of, 153))
    assert "loan-stale" not in str(macros.freshness(t.as_of, 150))
    assert "Data as of 31 Jul 2026" in str(macros.freshness(t.as_of, 10))


def test_unknown_balance_loan_listed_and_excluded(client, session):
    from app.models.debt import DebtDirection, DebtKind
    from decimal import Decimal
    _seed(session)
    session.add(Debt(kind=DebtKind.FORMAL, direction=DebtDirection.OWED_BY_US, original_amount=0.0,
                     current_balance=Decimal("0"), name="Mystery loan", external_number="777", status="active"))
    session.commit()
    html = client.get("/financials/loans").text
    assert "Mystery loan" in html
    assert "balance unknown" in html
    assert "totals exclude 1 loan(s) with unknown balance" in html
    assert position_totals(session, date(2026, 8, 1)).debt_total == 693.0


def test_informal_debt_section(client, session):
    _seed(session)
    doc = _doc(session, 9)
    t = Transaction(document_id=doc.id, provider="TRF P/ JOAO", category=Category.OTHER_EXPENSE, amount=300.0,
                    transaction_type=TransactionType.DEBIT)
    session.add(t)
    session.commit()
    r = client.post(f"/financials/transactions/{t.id}/link-debt", data={"direction": "owed_to_us", "person_name": "Joao"})
    assert r.status_code == 200
    debt = session.exec(select(Debt).where(Debt.external_number.is_(None))).one()
    html = client.get("/financials/loans").text
    assert "Joao" in html and f"/financials/transactions?debt_id={debt.id}" in html
    # owed to us (300) is netted against the loan; the total is never negative
    assert position_totals(session, date(2026, 8, 1)).debt_total == 393.0


def test_detail_lists_instalments_with_partial_payments_summed(client, session):
    debt = _seed(session)
    r = client.get(f"/financials/loans/{debt.id}")
    assert r.status_code == 200
    html = r.text
    assert "2 Jul 2026" in html and "2 Jun 2026" in html
    assert "307.00" in html and "79.00" in html and "12.50" in html and "693.00" in html
    assert html.index("2 Jul 2026") < html.index("2 Jun 2026")  # newest first
    assert html.count("<tr class=\"loan-inst\"") == 2


def test_detail_unknown_and_informal_are_404(client, session):
    _seed(session)
    assert client.get("/financials/loans/9999").status_code == 404
    from app.models.debt import DebtKind
    from decimal import Decimal
    d = Debt(kind=DebtKind.INFORMAL, original_amount=10.0, current_balance=Decimal("10"))
    session.add(d)
    session.commit()
    session.refresh(d)
    assert client.get(f"/financials/loans/{d.id}").status_code == 404


def test_empty_state(client):
    r = client.get("/financials/loans")
    assert r.status_code == 200
    assert "No loan or savings data yet" in r.text
    assert 'href="/financials/loans/upload"' in r.text


def test_nav_link_present(client):
    assert 'href="/financials/loans"' in client.get("/").text


# ---- Task 8: multi-PDF upload, assignment, reminder ---------------------

import json  # noqa: E402

import pytest  # noqa: E402

from app.models.document import DocumentStatus  # noqa: E402
from app.models.position import LoanMovement, PositionExtraction  # noqa: E402
from app.services import position_extraction as pe  # noqa: E402
from app.services.statement_reminder import REMINDER_TEXT  # noqa: E402
from app.services.storage import content_hash, save_file  # noqa: E402
from app.services.taxonomy import ensure_taxonomy  # noqa: E402
from tests.fakes.fake_gateway import FakeGateway  # noqa: E402

PDF = b"%PDF-1.4 synthetic "


def _reply(text):
    return {"text": text, "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}


def _stmt(as_of, remaining, rows, opening, number="900100201"):
    return _reply(json.dumps({"as_of": as_of, "loans": [{
        "number": number, "label": "CREDITO HABITACAO", "rate_percent": 2.5, "term_months": 300,
        "capital_granted": 5000.0, "start_date": "2024-01-15", "capital_remaining": remaining,
        "opening_balance": opening, "rows": rows}], "funds": [], "balances": []}))


def _srows(first, bal):
    out = []
    for i, n in enumerate((first, first + 1)):
        d = f"2026-0{1 + first // 38 * 2}-0{2 + i}"
        out.append({"date": d, "instalment_number": n, "component": "capital", "amount": 100.0,
                    "balance_after": bal - 100.0 * (i + 1)})
        out.append({"date": d, "instalment_number": n, "component": "interest", "amount": 20.0, "balance_after": None})
    return out


STMT_A = _stmt("2026-01-31", 700.0, _srows(36, 900.0), 900.0)
STMT_B = _stmt("2026-03-31", 500.0, _srows(38, 700.0), 700.0)


def _hist(numbers, capital=100.0, interest=20.0, first=35):
    rows = []
    for n in numbers:
        bal = 1000.0 - capital * (n - first + 1)
        d = date(2026, 1, 10 + n - first).isoformat()
        rows.append({"date": d, "instalment_number": n, "component": "capital", "amount": capital, "balance_after": bal})
        rows.append({"date": d, "instalment_number": n, "component": "interest", "amount": interest, "balance_after": None})
    return _reply(json.dumps({"rows": rows}))


@pytest.fixture()
def gateway(monkeypatch):
    def install(results):
        fake = FakeGateway(results)
        monkeypatch.setattr(pe, "get_gateway", lambda: fake)
        return fake
    return install


def _file(name, tag=""):
    return (name, PDF + name.encode() + tag.encode(), "application/pdf")


def _counts(session):
    session.expire_all()
    return (len(session.exec(select(LoanMovement)).all()), len(session.exec(select(Debt)).all()),
            len(session.exec(select(PositionExtraction)).all()))


def test_upload_page_has_two_multi_pdf_inputs(client):
    r = client.get("/financials/loans/upload")
    assert r.status_code == 200
    html = r.text
    assert html.count('type="file"') == 2 and html.count("multiple") >= 2 and html.count('accept=".pdf"') == 2
    assert 'name="statements"' in html and 'name="histories"' in html
    assert "Loan history printouts" in html and "Consulta Movimentos" in html


def test_upload_two_statements_and_two_histories_statements_first(client, session, gateway):
    fake = gateway([STMT_A, STMT_B, _hist([35, 36, 37]), _hist([33, 34, 35, 36, 37])])
    files = [("histories", _file("h1.pdf")), ("histories", _file("h2.pdf")),
             ("statements", _file("s1.pdf")), ("statements", _file("s2.pdf"))]  # histories listed first
    r = client.post("/financials/loans/upload", files=files)
    assert r.status_code == 200
    for name in ("s1.pdf", "s2.pdf", "h1.pdf", "h2.pdf"):
        assert name in r.text
    assert [q["system"] for q in fake.requests] == [pe._POSITIONS_SYSTEM_PROMPT] * 2 + [pe._LOAN_HISTORY_SYSTEM_PROMPT] * 2
    assert "1 loan" in r.text and "instalment" in r.text
    session.expire_all()
    nums = {m.instalment_number for m in session.exec(select(LoanMovement)).all()}
    assert {33, 34, 35, 36, 37, 38, 39} <= nums
    assert len(session.exec(select(Debt)).all()) == 1


def test_resubmitting_an_ordinary_statement_document_yields_positions(client, session, gateway):
    content = PDF + b"already-a-statement"
    doc = Document(filename="old.pdf", file_path=save_file("old.pdf", content), content_hash=content_hash(content),
                   source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED, domain="financials",
                   category="statement")
    session.add(doc)
    session.commit()
    gateway([STMT_A])
    r = client.post("/financials/loans/upload", files=[("statements", ("old.pdf", content, "application/pdf"))])
    assert r.status_code == 200 and "read" in r.text.lower()
    session.expire_all()
    assert len(session.exec(select(LoanMovement)).all()) == 2


def test_duplicate_of_incompatible_document_is_reported_not_processed(client, session, gateway):
    content = PDF + b"a-bill"
    doc = Document(filename="bill.pdf", file_path=save_file("bill.pdf", content), content_hash=content_hash(content),
                   source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED, domain="financials", category="bill")
    session.add(doc)
    session.commit()
    fake = gateway([])
    r = client.post("/financials/loans/upload", files=[("statements", ("bill.pdf", content, "application/pdf"))])
    assert r.status_code == 200 and "already uploaded as bill" in r.text and fake.requests == []


def test_same_printout_twice_reports_already_read_and_changes_nothing(client, session, gateway):
    fake = gateway([STMT_A, _hist([35, 36, 37])])
    client.post("/financials/loans/upload", files=[("statements", _file("s1.pdf"))])
    h = ("histories", _file("h1.pdf"))
    client.post("/financials/loans/upload", files=[h])
    before = _counts(session)
    r = client.post("/financials/loans/upload", files=[h])
    assert r.status_code == 200 and "already read" in r.text
    assert _counts(session) == before and len(fake.requests) == 2


def test_unmatched_printout_needs_a_loan_with_assignment_form(client, session, gateway):
    gateway([_hist([35, 36, 37])])
    r = client.post("/financials/loans/upload", files=[("histories", _file("h9.pdf"))])
    assert "needs a loan" in r.text.lower()
    ext = session.exec(select(PositionExtraction)).one()
    assert ext.status == "needs_loan"
    assert 'action="/financials/loans/assign"' in r.text and f'value="{ext.id}"' in r.text
    for field in ('name="number"', 'name="name"', 'name="loan_type"'):
        assert field in r.text


def test_assign_new_loan_creates_it_fills_movements_and_links_transactions(client, session, gateway):
    ensure_taxonomy(session)
    from app.models.transaction import Transaction, TransactionType
    doc = Document(filename="b.pdf", file_path="/tmp/b.pdf", content_hash="bank-doc", source=DocumentSource.MANUAL)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    txn = Transaction(document_id=doc.id, provider="COB.REC.31.900100299/ 35", amount=120.0,
                      transaction_type=TransactionType.DEBIT)
    session.add(txn)
    session.commit()
    gateway([_hist([35, 36, 37])])
    client.post("/financials/loans/upload", files=[("histories", _file("h9.pdf"))])
    ext = session.exec(select(PositionExtraction)).one()
    r = client.post("/financials/loans/assign", data={
        "extraction_id": ext.id, "debt_id": "new", "number": "9001 00299", "name": "Car loan", "loan_type": "personal"})
    assert r.status_code == 200, r.text
    session.expire_all()
    debt = session.exec(select(Debt).where(Debt.external_number == "900100299")).one()
    assert debt.name == "Car loan" and debt.loan_type == "personal"
    assert len(session.exec(select(LoanMovement).where(LoanMovement.debt_id == debt.id)).all()) == 3
    assert session.get(PositionExtraction, ext.id).status == "ok"
    assert session.get(Transaction, txn.id).debt_id == debt.id


def test_assign_to_existing_loan(client, session, gateway):
    debt = _seed(session)
    gateway([_hist([30, 31, 32], capital=150.0, first=30)])
    client.post("/financials/loans/upload", files=[("histories", _file("hx.pdf"))])
    ext = session.exec(select(PositionExtraction)).one()
    assert ext.status == "needs_loan"
    r = client.post("/financials/loans/assign", data={"extraction_id": ext.id, "debt_id": str(debt.id)})
    assert r.status_code == 200
    session.expire_all()
    assert session.get(PositionExtraction, ext.id).status == "ok"


def test_assign_new_loan_with_existing_number_reuses_the_debt(client, session, gateway):
    debt = _seed(session)
    gateway([_hist([30, 31, 32], capital=150.0, first=30)])
    client.post("/financials/loans/upload", files=[("histories", _file("hx.pdf"))])
    ext = session.exec(select(PositionExtraction)).one()
    r = client.post("/financials/loans/assign", data={
        "extraction_id": ext.id, "debt_id": "new", "number": debt.external_number, "name": "Dup", "loan_type": "mortgage"})
    assert r.status_code == 200
    session.expire_all()
    assert len(session.exec(select(Debt).where(Debt.external_number == debt.external_number)).all()) == 1


@pytest.mark.parametrize("data,status", [
    ({"debt_id": "new", "number": "123", "name": "X", "loan_type": "mortgage"}, 400),
    ({"debt_id": "new", "number": "12345678", "name": " ", "loan_type": "mortgage"}, 400),
    ({"debt_id": "new", "number": "12345678", "name": "X", "loan_type": "boat"}, 400),
    ({"debt_id": "new", "number": "1234abcd5", "name": "X", "loan_type": "mortgage"}, 400),
])
def test_assign_validation(client, session, gateway, data, status):
    gateway([_hist([35, 36, 37])])
    client.post("/financials/loans/upload", files=[("histories", _file("h9.pdf"))])
    ext = session.exec(select(PositionExtraction)).one()
    r = client.post("/financials/loans/assign", data={"extraction_id": ext.id, **data})
    assert r.status_code == status


def test_assign_unknown_extraction_404_and_wrong_state_400(client, session, gateway):
    assert client.post("/financials/loans/assign", data={
        "extraction_id": 999, "debt_id": "new", "number": "12345678", "name": "X", "loan_type": "mortgage"}).status_code == 404
    gateway([STMT_A])
    client.post("/financials/loans/upload", files=[("statements", _file("s1.pdf"))])
    ext = session.exec(select(PositionExtraction)).one()
    assert ext.status == "ok"
    assert client.post("/financials/loans/assign", data={
        "extraction_id": ext.id, "debt_id": "new", "number": "12345678", "name": "X", "loan_type": "mortgage"}).status_code == 400


def test_non_pdf_is_rejected_per_file_without_crashing(client, session, gateway):
    gateway([STMT_A])
    r = client.post("/financials/loans/upload", files=[
        ("statements", ("notes.txt", b"hello", "text/plain")),
        ("statements", ("fake.pdf", b"not really a pdf", "application/pdf")),
        ("statements", _file("s1.pdf")),
    ])
    assert r.status_code == 200
    assert "notes.txt" in r.text and "fake.pdf" in r.text and "rejected" in r.text.lower()
    assert len(session.exec(select(LoanMovement)).all()) == 2   # the good file still processed


def test_one_failing_file_does_not_abort_the_rest(client, session, gateway):
    gateway([_reply("not json"), STMT_A])
    r = client.post("/financials/loans/upload", files=[("statements", _file("bad.pdf")), ("statements", _file("s1.pdf"))])
    assert r.status_code == 200 and "failed" in r.text.lower()
    assert len(session.exec(select(LoanMovement)).all()) == 2


def test_more_than_twelve_files_is_400(client, gateway):
    gateway([])
    files = [("statements", _file(f"s{i}.pdf")) for i in range(13)]
    assert client.post("/financials/loans/upload", files=files).status_code == 400


def test_overview_reminder_item_only_when_due(client, session):
    from app.models.position import BalanceSnapshot
    d = _doc(session, 7)
    session.add(BalanceSnapshot(as_of=date.today(), kind="deposit", label="D", amount=1.0, document_id=d.id))
    session.commit()
    assert REMINDER_TEXT not in client.get("/").text
    for b in session.exec(select(BalanceSnapshot)).all():
        b.as_of = date(2020, 1, 31)
        session.add(b)
    session.commit()
    html = client.get("/").text
    assert REMINDER_TEXT in html and 'href="/financials/loans/upload"' in html
    assert "fc-attn-overdue" in html   # long past its deadline


def test_index_shows_effective_rate_and_applied_note(client, session):
    apply_positions(session, _doc(session), _positions(loans=[_loan(rate_percent=2.0, next_rate_percent=3.5)]))
    html = client.get("/financials/loans").text
    assert "3.500%" in html
    assert "applied last instalment: 2.000%" in html
    apply_positions(session, _doc(session, 2), _positions(
        as_of=date(2026, 8, 31), loans=[_loan(rate_percent=3.5, next_rate_percent=3.5)]))
    assert "applied last instalment" not in client.get("/financials/loans").text


def test_paid_totals_are_labelled_as_recorded_instalments_with_since_date(client, session):
    _seed(session)
    html = client.get("/financials/loans").text.lower()
    assert "paid to date" not in html
    assert "paid in recorded instalments (since 2 jun 2026)" in html
    assert "capital repaid so far" in html and "€4,307" in html  # granted 5000 - remaining 693


def test_no_capital_repaid_line_without_granted_or_balance(client, session):
    _seed(session, loans=[_loan(capital_granted=None)])
    html = client.get("/financials/loans").text.lower()
    assert "paid in recorded instalments" in html
    assert "capital repaid so far" not in html


# ---- I1: duplicate uploads never corrupt the document status ---------------


def _dup_doc(session, name, category, status):
    content = PDF + name.encode()
    doc = Document(filename=name, file_path=save_file(name, content), content_hash=content_hash(content),
                   source=DocumentSource.MANUAL, status=status, domain="financials", category=category)
    session.add(doc)
    session.commit()
    return doc, content


def test_failed_positions_read_never_flips_an_ordinary_statement_document(client, session, gateway):
    doc, content = _dup_doc(session, "old.pdf", "statement", DocumentStatus.PROCESSED)
    gateway([_reply("not json")])
    r = client.post("/financials/loans/upload", files=[("statements", ("old.pdf", content, "application/pdf"))])
    assert r.status_code == 200 and "failed" in r.text.lower()
    session.expire_all()
    assert session.get(Document, doc.id).status == DocumentStatus.PROCESSED
    assert session.exec(select(PositionExtraction)).one().status == "failed"


def test_failed_then_successful_positions_document_ends_processed(client, session, gateway):
    gateway([_reply("not json"), STMT_A])
    f = ("statements", _file("s1.pdf"))
    client.post("/financials/loans/upload", files=[f])
    session.expire_all()
    doc = session.exec(select(Document).where(Document.filename == "s1.pdf")).one()
    assert doc.status == DocumentStatus.NEEDS_ATTENTION
    r = client.post("/financials/loans/upload", files=[f])  # retried: one more gateway call
    assert r.status_code == 200
    session.expire_all()
    doc = session.get(Document, doc.id)
    assert doc.status == DocumentStatus.PROCESSED and doc.failure_reason is None
    assert session.exec(select(PositionExtraction)).one().status == "ok"


# ---- I4: a printout of another tranche is never merged into the wrong loan --


def test_assign_rejects_overlapping_instalments_that_disagree(client, session, gateway):
    debt = _seed(session)  # instalments 36 (capital 300) and 37 (capital 307)
    gateway([_hist([35, 36, 37], capital=150.0)])  # another tranche: same numbers, other amounts
    client.post("/financials/loans/upload", files=[("histories", _file("hx.pdf"))])
    ext = session.exec(select(PositionExtraction)).one()
    before = _counts(session)
    r = client.post("/financials/loans/assign", data={"extraction_id": ext.id, "debt_id": str(debt.id)})
    assert r.status_code == 400 and "instalment" in r.text.lower() and "different" in r.text.lower()
    assert 'action="/financials/loans/assign"' in r.text  # pending row re-rendered
    assert _counts(session) == before
    session.expire_all()
    assert session.get(PositionExtraction, ext.id).status == "needs_loan"


# ---- M6: per-file size cap --------------------------------------------------


def test_oversized_file_is_rejected_per_file_and_the_rest_still_processed(client, session, gateway, monkeypatch):
    from app.routers import loans as loans_router
    monkeypatch.setattr(loans_router, "MAX_FILE_BYTES", 200)
    gateway([STMT_A])
    big = ("big.pdf", PDF + b"x" * 500, "application/pdf")
    r = client.post("/financials/loans/upload", files=[("statements", big), ("statements", _file("s1.pdf"))])
    assert r.status_code == 200
    assert "big.pdf" in r.text and "too large" in r.text.lower()
    assert len(session.exec(select(LoanMovement)).all()) == 2


# --- Block A: red flags and rate history ----------------------------------

def _flag(session, debt, kind="interest_only", ref="inst:3", message="Instalment 3 (02 Mar 2026): interest only", **kw):
    from app.models.position import LoanAlert
    a = LoanAlert(debt_id=debt.id, kind=kind, ref=ref, message=message, detected_on=date(2026, 10, 6), **kw)
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def test_no_alerts_no_badge_no_red_block(client, session):
    _seed(session)
    html = client.get("/financials/loans").text
    assert 'class="loan-badge"' not in html and '<div class="loan-flags"' not in html


def test_index_shows_red_block_and_badge_with_unacknowledged_count(client, session):
    debt = _seed(session)
    a = _flag(session, debt)
    _flag(session, debt, ref="inst:4", message="Instalment 4: interest only")
    html = client.get("/financials/loans").text
    assert "Red flags" in html and "Instalment 3 (02 Mar 2026): interest only" in html
    assert 'class="loan-badge"' in html and ">1 red flag<" in html  # grouped per (loan, kind)
    assert f'action="/financials/loans/alerts/{a.id}/ack"' in html
    assert "06 Oct 2026" in html or "6 Oct 2026" in html


def test_acknowledge_redirects_stores_note_and_collapses(client, session):
    from app.models.position import LoanAlert
    debt = _seed(session)
    a = _flag(session, debt)
    r = client.post(f"/financials/loans/alerts/{a.id}/ack", data={"note": "bank error, known"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/financials/loans"
    session.expire_all()
    row = session.get(LoanAlert, a.id)
    assert row.acknowledged and row.ack_note == "bank error, known"
    html = client.get("/financials/loans").text
    assert 'class="loan-badge"' not in html and "bank error, known" in html
    assert "<details" in html and "Acknowledged" in html


def test_acknowledge_without_note_and_unknown_id_404(client, session):
    debt = _seed(session)
    a = _flag(session, debt)
    assert client.post(f"/financials/loans/alerts/{a.id}/ack", follow_redirects=False).status_code == 303
    assert client.post("/financials/loans/alerts/9999/ack", follow_redirects=False).status_code == 404


def test_acknowledge_next_is_limited_to_loans_pages(client, session):
    debt = _seed(session)
    a = _flag(session, debt)
    r = client.post(f"/financials/loans/alerts/{a.id}/ack", data={"next": f"/financials/loans/{debt.id}"},
                    follow_redirects=False)
    assert r.headers["location"] == f"/financials/loans/{debt.id}"
    b = _flag(session, debt, ref="inst:9")
    r = client.post(f"/financials/loans/alerts/{b.id}/ack", data={"next": "https://evil.example/"},
                    follow_redirects=False)
    assert r.headers["location"] == "/financials/loans"


def test_detail_shows_red_flags_and_overview_item_links_to_loan(client, session):
    debt = _seed(session)
    _flag(session, debt)
    assert "Red flags" in client.get(f"/financials/loans/{debt.id}").text
    html = client.get("/").text
    assert f"{debt.name}: Instalment 3" in html and f'href="/financials/loans/{debt.id}"' in html
    assert "Open loan" in html and "fc-attn-alert" in html


def test_overview_has_no_loan_item_without_alerts(client, session):
    _seed(session)
    assert "Open loan" not in client.get("/").text


def test_rate_history_table_highlights_a_changed_spread(client, session):
    debt = _seed(session)
    apply_positions(session, _doc(session, 20), _positions(
        as_of=date(2026, 8, 31), loans=[_loan(next_rate_percent=3.0, next_indexante_percent=1.8, next_spread_percent=1.2)]))
    apply_positions(session, _doc(session, 21), _positions(
        as_of=date(2026, 9, 30), loans=[_loan(next_rate_percent=3.1, next_indexante_percent=1.8, next_spread_percent=1.3)]))
    html = client.get(f"/financials/loans/{debt.id}").text
    assert "Rate history" in html
    assert html.index("31 Aug 2026") < html.index("30 Sep 2026", html.index("Rate history"))
    assert "1.800" in html and "1.200" in html and "1.300" in html and "3.100" in html
    assert html.count('class="loan-spread-bad"') == 1  # only the changed spread cell


def test_rate_history_absent_for_a_loan_without_snapshots(client, session):
    from app.services.position_store import apply_loan_history, create_loan_from_assignment
    from app.services.position_extraction import Instalment
    debt = create_loan_from_assignment(session, "900100999", "History only", "personal")
    apply_loan_history(session, _doc(session, 30), debt, [Instalment(1, date(2026, 1, 2), 10.0, 5.0, 0.0, 90.0)])
    assert "Rate history" not in client.get(f"/financials/loans/{debt.id}").text


# ---- Block B: both upload boxes are independent and optional ---------------

def test_upload_page_boxes_are_optional_and_copy_says_so(client):
    html = client.get("/financials/loans/upload").text
    assert "required" not in html
    assert "Upload statements, loan printouts, or both" in html


def test_upload_statements_only(client, session, gateway):
    fake = gateway([STMT_A])
    r = client.post("/financials/loans/upload", files=[("statements", _file("s1.pdf"))])
    assert r.status_code == 200 and "s1.pdf" in r.text
    assert [q["system"] for q in fake.requests] == [pe._POSITIONS_SYSTEM_PROMPT]
    assert _counts(session)[1] == 1


def test_upload_printouts_only(client, session, gateway):
    fake = gateway([_hist([35, 36, 37])])
    r = client.post("/financials/loans/upload", files=[("histories", _file("h1.pdf"))])
    assert r.status_code == 200 and "h1.pdf" in r.text
    assert [q["system"] for q in fake.requests] == [pe._LOAN_HISTORY_SYSTEM_PROMPT]


def test_upload_both(client, session, gateway):
    fake = gateway([STMT_A, _hist([35, 36, 37])])
    r = client.post("/financials/loans/upload", files=[("statements", _file("s1.pdf")), ("histories", _file("h1.pdf"))])
    assert r.status_code == 200 and "s1.pdf" in r.text and "h1.pdf" in r.text
    assert len(fake.requests) == 2


def test_upload_neither_is_400_with_a_clear_message(client, gateway):
    gateway([])
    r = client.post("/financials/loans/upload")
    assert r.status_code == 400 and "choose at least one file" in r.text.lower()


def test_empty_filename_parts_are_ignored(client, session, gateway):
    """A browser sends an empty file part for an unfilled input."""
    fake = gateway([STMT_A])
    empty = ("", b"", "application/octet-stream")
    r = client.post("/financials/loans/upload", files=[("statements", _file("s1.pdf")), ("histories", empty)])
    assert r.status_code == 200 and "s1.pdf" in r.text and len(fake.requests) == 1
    r = client.post("/financials/loans/upload", files=[("statements", empty), ("histories", empty)])
    assert r.status_code == 400 and "choose at least one file" in r.text.lower()


# ---- Block C: insurance beside the instalment -------------------------------

def test_loan_page_shows_linked_insurance_paid_and_last_premium(client, session):
    from app.services.loan_insurance import link_insurance_transaction
    ensure_taxonomy(session)
    debt = _seed(session)
    doc = _doc(session, 9)
    for paid, amount in ((date(2026, 7, 3), 12.5), (date(2026, 8, 3), 13.0)):
        t = Transaction(document_id=doc.id, provider="SEG VIDA 15.000001-2026/08/21", amount=amount,
                        transaction_type=TransactionType.DEBIT, paid_date=paid)
        session.add(t)
        session.flush()
        assert link_insurance_transaction(session, t) is True
    session.commit()
    assert session.exec(select(Transaction).where(Transaction.debt_id == debt.id)).all()
    html = client.get("/financials/loans").text
    assert "insurance paid (linked debits)" in html.lower() and "€25.50" in html
    assert "last premium" in html and "€13.00" in html
    detail = client.get(f"/financials/loans/{debt.id}").text
    assert "€25.50" in detail


# --- Block D: informal loans section and its forms -----------------------

def _informal_debt(session, direction="owed_to_us", name="Test Person", opening=None):
    from app.models.debt import DebtDirection
    from app.services.debt_ledger import create_informal_debt
    d = create_informal_debt(session, name, DebtDirection(direction), opening_amount=opening, entry_date=date(2026, 1, 5))
    session.commit()
    session.refresh(d)
    return d


def test_informal_section_shows_ledger_with_running_balances(client, session):
    from app.services import debt_ledger
    d = _informal_debt(session, opening=1000.0)
    debt_ledger.add_entry(session, d, "repayment", 300.0, date(2026, 2, 1), note="first back")
    t = Transaction(document_id=_doc(session, 40).id, provider="TRF P/ TEST", amount=500.0,
                    transaction_type=TransactionType.DEBIT, paid_date=date(2026, 3, 1))
    session.add(t)
    session.commit()
    debt_ledger.link_transaction_as_entry(session, d, t, None)
    session.commit()
    html = client.get("/financials/loans").text
    assert "Test Person" in html and "they owe us" in html
    assert "€1,500.00" in html  # advances total
    assert "€300.00" in html    # repayments total
    assert "€1,200.00" in html  # balance and last running balance
    assert "€700.00" in html    # running balance after the repayment
    assert "first back" in html
    assert f"/financials/transactions?transaction_id={t.id}" in html
    assert "5 Jan 2026" in html and "1 Mar 2026" in html  # last activity
    table = html[html.index('<table class="loan-table loan-ledger"'):]
    assert table.index("5 Jan 2026") < table.index("1 Feb 2026") < table.index("1 Mar 2026")
    assert f"/financials/loans/debts/{d.id}/entries" in html
    # only the manual entries carry a delete button
    assert html.count("/delete") == 2


def test_informal_section_shows_overpaid(client, session):
    from app.services import debt_ledger
    d = _informal_debt(session, "owed_by_us", opening=100.0)
    debt_ledger.add_entry(session, d, "repayment", 130.0, date(2026, 2, 1))
    session.commit()
    html = client.get("/financials/loans").text
    assert "overpaid by €30.00" in html


def test_add_entry_route_adds_and_redirects(client, session):
    d = _informal_debt(session, opening=100.0)
    r = client.post(f"/financials/loans/debts/{d.id}/entries", follow_redirects=False,
                    data={"kind": "adjust_down", "amount": "25.50", "entry_date": "2026-02-02", "note": "<b>cash</b>"})
    assert r.status_code == 303 and r.headers["location"] == "/financials/loans"
    session.refresh(d)
    assert d.current_balance == Decimal("74.50")
    html = client.get("/financials/loans").text
    assert "&lt;b&gt;cash&lt;/b&gt;" in html and "<b>cash</b>" not in html


@pytest.mark.parametrize("data", [
    {"kind": "advance", "amount": "0", "entry_date": "2026-02-02"},
    {"kind": "advance", "amount": "-5", "entry_date": "2026-02-02"},
    {"kind": "advance", "amount": "nan", "entry_date": "2026-02-02"},
    {"kind": "advance", "amount": "inf", "entry_date": "2026-02-02"},
    {"kind": "advance", "amount": "abc", "entry_date": "2026-02-02"},
    {"kind": "gift", "amount": "5", "entry_date": "2026-02-02"},
    {"kind": "advance", "amount": "5", "entry_date": "02/02/2026"},
    {"kind": "advance", "amount": "5", "entry_date": ""},
])
def test_add_entry_validation_errors_change_nothing(client, session, data):
    d = _informal_debt(session, opening=100.0)
    r = client.post(f"/financials/loans/debts/{d.id}/entries", data=data)
    assert r.status_code == 400
    session.refresh(d)
    assert d.current_balance == Decimal("100.00")


def test_add_entry_to_a_statement_loan_or_unknown_debt_is_404(client, session):
    loan = _seed(session)
    ok = {"kind": "advance", "amount": "5", "entry_date": "2026-02-02"}
    assert client.post(f"/financials/loans/debts/{loan.id}/entries", data=ok).status_code == 404
    assert client.post("/financials/loans/debts/9999/entries", data=ok).status_code == 404
    assert client.post(f"/financials/loans/debts/{loan.id}/entries/1/delete").status_code == 404


def test_delete_manual_entry_but_not_a_linked_one(client, session):
    from app.models.position import DebtEntry
    from app.services import debt_ledger
    d = _informal_debt(session, opening=100.0)
    manual = session.exec(select(DebtEntry)).one()
    t = Transaction(document_id=_doc(session, 41).id, provider="TRF P/ TEST", amount=50.0,
                    transaction_type=TransactionType.DEBIT, paid_date=date(2026, 3, 1))
    session.add(t)
    session.commit()
    linked = debt_ledger.link_transaction_as_entry(session, d, t, None)
    session.commit()
    assert client.post(f"/financials/loans/debts/{d.id}/entries/{linked.id}/delete").status_code == 400
    r = client.post(f"/financials/loans/debts/{d.id}/entries/{manual.id}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/financials/loans"
    session.refresh(d)
    assert d.current_balance == Decimal("50.00")
    # an entry of another debt cannot be deleted through this debt's URL
    other = _informal_debt(session, name="Test Other", opening=10.0)
    foreign = session.exec(select(DebtEntry).where(DebtEntry.debt_id == other.id)).one()
    assert client.post(f"/financials/loans/debts/{d.id}/entries/{foreign.id}/delete").status_code == 404


def test_new_informal_loan_form(client, session):
    from app.models.position import DebtEntry
    r = client.post("/financials/loans/debts/new", follow_redirects=False, data={
        "person_name": " Test Person ", "direction": "owed_by_us", "opening_amount": "250",
        "entry_date": "2026-01-05", "note": "cash"})
    assert r.status_code == 303 and r.headers["location"] == "/financials/loans"
    d = session.exec(select(Debt).where(Debt.external_number.is_(None))).one()
    assert d.direction.value == "owed_by_us" and d.current_balance == Decimal("250.00")
    e = session.exec(select(DebtEntry)).one()
    assert e.transaction_id is None and e.kind == "advance" and e.entry_date == date(2026, 1, 5)
    # no opening amount: an empty loan, ready for entries
    r = client.post("/financials/loans/debts/new", follow_redirects=False,
                    data={"person_name": "Test Empty", "direction": "owed_to_us"})
    assert r.status_code == 303
    assert len(session.exec(select(DebtEntry)).all()) == 1


@pytest.mark.parametrize("data", [
    {"person_name": "", "direction": "owed_to_us"},
    {"person_name": "   ", "direction": "owed_to_us"},
    {"person_name": "x" * 81, "direction": "owed_to_us"},
    {"person_name": "Test Person", "direction": "sideways"},
    {"person_name": "Test Person", "direction": ""},
    {"person_name": "Test Person", "direction": "owed_to_us", "opening_amount": "0"},
    {"person_name": "Test Person", "direction": "owed_to_us", "opening_amount": "-3"},
    {"person_name": "Test Person", "direction": "owed_to_us", "opening_amount": "nan"},
    {"person_name": "Test Person", "direction": "owed_to_us", "opening_amount": "5", "entry_date": "yesterday"},
])
def test_new_informal_loan_validation(client, session, data):
    assert client.post("/financials/loans/debts/new", data=data).status_code == 400
    assert session.exec(select(Debt).where(Debt.external_number.is_(None))).first() is None


def test_ledger_rows_escape_the_person_name(client, session):
    _informal_debt(session, name="<script>x</script>", opening=10.0)
    html = client.get("/financials/loans").text
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html


# --- fix wave: grouped red flags ------------------------------------------

def test_fifteen_alerts_are_one_group_line_one_badge_and_one_group_form(client, session):
    debt = _seed(session)
    ids = [_flag(session, debt, ref=f"inst:{n}", message=f"Instalment {n} (02 Mar 2026): interest only (€20.00 interest)").id
           for n in range(1, 16)]
    html = client.get("/financials/loans").text
    assert ">1 red flag<" in html
    assert "15 interest-only instalments (nº 1–15, total interest €300.00)" in html
    assert html.count('action="/financials/loans/alerts/ack-group"') == 1
    assert all(f'action="/financials/loans/alerts/{i}/ack"' in html for i in ids)  # expandable individual list
    assert 'name="kind" value="interest_only"' in html and f'name="debt_id" value="{debt.id}"' in html


def test_ack_group_route_acknowledges_the_group_only(client, session):
    from app.models.position import LoanAlert
    debt = _seed(session)
    for n in range(1, 4):
        _flag(session, debt, ref=f"inst:{n}")
    other = _flag(session, debt, kind="spread_changed", ref="snap:1", message="Spread changed")
    r = client.post("/financials/loans/alerts/ack-group", data={"debt_id": debt.id, "kind": "interest_only", "note": "known"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/financials/loans"
    session.expire_all()
    rows = session.exec(select(LoanAlert)).all()
    assert {a.ref for a in rows if a.acknowledged} == {"inst:1", "inst:2", "inst:3"}
    assert not session.get(LoanAlert, other.id).acknowledged


def test_ack_group_next_is_safe_and_per_alert_ack_still_works(client, session):
    debt = _seed(session)
    a = _flag(session, debt)
    r = client.post("/financials/loans/alerts/ack-group",
                    data={"debt_id": debt.id, "kind": "interest_only", "next": "https://evil.example/"},
                    follow_redirects=False)
    assert r.headers["location"] == "/financials/loans"
    b = _flag(session, debt, ref="inst:8")
    assert client.post(f"/financials/loans/alerts/{b.id}/ack", follow_redirects=False).status_code == 303
    assert a.id != b.id


def test_ack_group_unknown_debt_404_unknown_kind_400_and_long_note_400(client, session):
    debt = _seed(session)
    _flag(session, debt)
    assert client.post("/financials/loans/alerts/ack-group", data={"debt_id": 9999, "kind": "interest_only"}).status_code == 404
    assert client.post("/financials/loans/alerts/ack-group", data={"debt_id": debt.id, "kind": "nonsense"}).status_code == 400
    assert client.post("/financials/loans/alerts/ack-group",
                       data={"debt_id": debt.id, "kind": "interest_only", "note": "x" * 301}).status_code == 400


def test_single_ack_rejects_a_note_over_300_characters(client, session):
    from app.models.position import LoanAlert
    debt = _seed(session)
    a = _flag(session, debt)
    assert client.post(f"/financials/loans/alerts/{a.id}/ack", data={"note": "x" * 301}).status_code == 400
    session.expire_all()
    assert not session.get(LoanAlert, a.id).acknowledged
    assert client.post(f"/financials/loans/alerts/{a.id}/ack", data={"note": "x" * 300}, follow_redirects=False).status_code == 303
