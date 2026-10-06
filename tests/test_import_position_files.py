"""scripts/import_position_files.py: feeds PDFs through the Loans & Savings upload
pipeline. All data is synthetic; replies are built like tests/test_loans_router.py."""
import json
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.models.debt import Debt
from app.models.position import LoanMovement, PositionExtraction
from app.models.document import Document
from app.services.taxonomy import ensure_taxonomy
from tests.fakes.fake_gateway import FakeGateway

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import import_position_files as script  # noqa: E402

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


def _hist(numbers, capital=100.0, interest=20.0, first=35):
    rows = []
    for n in numbers:
        bal = 1000.0 - capital * (n - first + 1)
        d = date(2026, 1, 10 + n - first).isoformat()
        rows.append({"date": d, "instalment_number": n, "component": "capital", "amount": capital, "balance_after": bal})
        rows.append({"date": d, "instalment_number": n, "component": "interest", "amount": interest, "balance_after": None})
    return _reply(json.dumps({"rows": rows}))


@pytest.fixture(autouse=True)
def docs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "documents")


@pytest.fixture()
def factory(engine):
    return lambda: Session(engine)


def _pdf(tmp_path, name, tag=""):
    p = tmp_path / name
    p.write_bytes(PDF + name.encode() + tag.encode())
    return str(p)


def _run(factory, fake, *argv):
    return script.main(list(argv), session_factory=factory, gateway=fake)


def _lines(capsys):
    return capsys.readouterr().out.splitlines()


def test_statements_then_histories_in_that_order(factory, session, tmp_path, capsys):
    ensure_taxonomy(session)
    fake = FakeGateway([STMT_A, _hist([35, 36, 37])])
    h, s = _pdf(tmp_path, "h1.pdf"), _pdf(tmp_path, "s1.pdf")
    # histories named first on the command line, statements must still go first
    code = _run(factory, fake, "--histories", h, "--statements", s)
    assert code == 0
    out = _lines(capsys)
    assert out[0].startswith("OK") and "s1.pdf" in out[0]
    assert out[1].startswith("OK") and "h1.pdf" in out[1]
    # the history matched the loan created by the statement
    session.expire_all()
    assert len(session.exec(select(LoanMovement)).all()) >= 3


def test_request_order_statements_before_histories(factory, session, tmp_path):
    from app.services import position_extraction as pe
    fake = FakeGateway([STMT_A, _hist([35, 36, 37])])
    _run(factory, fake, "--histories", _pdf(tmp_path, "h1.pdf"), "--statements", _pdf(tmp_path, "s1.pdf"))
    assert [r["user"] for r in fake.requests] == [pe._POSITIONS_USER_PROMPT, pe._LOAN_HISTORY_USER_PROMPT]


def test_duplicate_file_reports_already(factory, tmp_path, capsys):
    fake = FakeGateway([STMT_A])
    s = _pdf(tmp_path, "s1.pdf")
    assert _run(factory, fake, "--statements", s) == 0
    capsys.readouterr()
    assert _run(factory, fake, "--statements", s) == 0
    out = _lines(capsys)
    assert out[0].startswith("ALREADY") and "s1.pdf" in out[0]
    assert len(fake.requests) == 1


def test_unmatched_printout_needs_loan_and_lists_extraction(factory, tmp_path, capsys):
    fake = FakeGateway([_hist([35, 36, 37])])
    assert _run(factory, fake, "--histories", _pdf(tmp_path, "h9.pdf")) == 0
    text = capsys.readouterr().out
    assert text.splitlines()[0].startswith("NEEDS_LOAN") and "h9.pdf" in text
    assert "--new-loan" in text and "extraction" in text


def test_new_loan_assigns_and_fills_movements(factory, session, tmp_path, capsys):
    ensure_taxonomy(session)
    fake = FakeGateway([_hist([35, 36, 37])])
    code = _run(factory, fake, "--histories", _pdf(tmp_path, "h9.pdf"), "--new-loan", "900100299:Car loan:personal")
    assert code == 0
    session.expire_all()
    debt = session.exec(select(Debt).where(Debt.external_number == "900100299")).one()
    assert debt.name == "Car loan" and debt.loan_type == "personal"
    assert len(session.exec(select(LoanMovement).where(LoanMovement.debt_id == debt.id)).all()) == 3
    assert session.exec(select(PositionExtraction)).one().status == "ok"
    assert "assigned" in capsys.readouterr().out.lower()


def test_new_loan_with_nothing_pending(factory, tmp_path, capsys):
    fake = FakeGateway([STMT_A])
    assert _run(factory, fake, "--statements", _pdf(tmp_path, "s1.pdf"), "--new-loan", "900100299:Car:other") == 0
    assert "nothing to assign" in capsys.readouterr().out


def test_two_pending_with_new_loan_exits_2_and_assigns_nothing(factory, session, tmp_path, capsys):
    fake = FakeGateway([_hist([35, 36, 37]), _hist([30, 31, 32], capital=150.0, first=30)])
    code = _run(factory, fake, "--histories", _pdf(tmp_path, "h1.pdf"), _pdf(tmp_path, "h2.pdf"),
                "--new-loan", "900100299:Car:other")
    assert code == 2
    session.expire_all()
    assert session.exec(select(Debt)).all() == []
    assert session.exec(select(LoanMovement)).all() == []
    assert {e.status for e in session.exec(select(PositionExtraction)).all()} == {"needs_loan"}


def test_dry_run_touches_nothing(factory, session, tmp_path, capsys):
    fake = FakeGateway([])
    code = _run(factory, fake, "--dry-run", "--statements", _pdf(tmp_path, "s1.pdf"),
                "--histories", _pdf(tmp_path, "h1.pdf"))
    assert code == 0 and fake.requests == []
    out = capsys.readouterr().out
    assert "s1.pdf" in out and "statements" in out and "histories" in out and "bytes" in out
    session.expire_all()
    assert session.exec(select(Document)).all() == []


def test_dry_run_flags_non_pdf(factory, tmp_path, capsys):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"hello")
    assert _run(factory, FakeGateway([]), "--dry-run", "--statements", str(p)) == 0
    assert "REJECTED" in capsys.readouterr().out


def test_non_pdf_is_rejected(factory, tmp_path, capsys):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"hello")
    fake = FakeGateway([])
    assert _run(factory, fake, "--statements", str(p)) == 0
    assert _lines(capsys)[0].startswith("REJECTED") and fake.requests == []
    assert p.exists()


def test_failed_file_exits_1(factory, tmp_path, capsys):
    fake = FakeGateway([_reply("not json")])
    assert _run(factory, fake, "--statements", _pdf(tmp_path, "bad.pdf")) == 1
    assert _lines(capsys)[0].startswith("FAILED")


def test_missing_path_is_an_error_and_nothing_runs(factory, tmp_path, capsys):
    fake = FakeGateway([])
    code = _run(factory, fake, "--statements", str(tmp_path / "nope.pdf"))
    assert code == 2 and fake.requests == []
    assert "nope.pdf" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["abc", "123:Name:mortgage", "900100299::mortgage", "900100299:Name:car",
                                 "9001002x9:Name:other", "900100299:Na:me:other"])
def test_bad_new_loan_format_exits_2(factory, tmp_path, bad, capsys):
    fake = FakeGateway([])
    assert _run(factory, fake, "--histories", _pdf(tmp_path, "h1.pdf"), "--new-loan", bad) == 2
    assert fake.requests == []


def test_no_files_is_a_usage_error(factory):
    assert _run(factory, FakeGateway([]), "--dry-run") == 2
