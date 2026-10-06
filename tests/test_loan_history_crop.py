"""Printouts list newest first and may be cut mid-instalment at the bottom: unless
provably complete, the oldest instalment is ignored. SYNTHETIC data only."""
import pytest
from sqlmodel import select

from app.models.debt import Debt
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.position import LoanMovement
from app.services import position_extraction as pe
from app.services.position_extraction import _parse_history, parse_loan_history_text
from app.services.position_store import (
    assign_history_to_loan, create_loan_from_assignment, process_loan_history_document,
)
from tests.fakes.fake_gateway import FakeGateway


def _l(d, n, comp, amount, bal):
    return f"{d} {d} {n} PRESTACAO - {comp} -{amount} EUR {bal} EUR\n"


FORMAL = "26-07-2023 26-07-2023 n/a FORMALIZACAO - CAPIT. 10.000,00 EUR 10.000,00 EUR\n"


def _inst(n, day, bal, cap="100,00"):
    d = f"{day:02d}-01-2026"
    return _l(d, n, "JUROS", "20,00", "0,00") + _l(d, n, "CAPIT.", cap, bal)


# newest first, like the real printout
TEXT = _inst(37, 12, "700,00") + _inst(36, 11, "800,00") + _inst(35, 10, "900,00")
CROPPED_TOP = _l("10-01-2026", 35, "CAPIT.", "50,00", "900,00")  # partial oldest piece, no interest


def _doc(session, tmp_path, n=1):
    path = tmp_path / f"h{n}.pdf"
    path.write_bytes(b"%PDF-1.4 x")
    d = Document(filename=f"h{n}.pdf", file_path=str(path), content_hash=f"crop-{n}", source=DocumentSource.MANUAL,
                 status=DocumentStatus.PENDING, category="loan_history")
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


async def _process(session, tmp_path, monkeypatch, text, n=1):
    monkeypatch.setattr(pe, "_pdf_text", lambda p: text)
    return await process_loan_history_document(session, _doc(session, tmp_path, n), gateway=FakeGateway([]))


def test_formalisation_line_is_recognised_without_warning():
    rows, warnings, formal = _parse_history(FORMAL + TEXT)
    assert formal and warnings == [] and len(rows) == 6
    assert parse_loan_history_text(FORMAL + TEXT)[1] == []


def test_annulment_lines_are_skipped_silently():
    text = "02-12-2024 02-12-2024 16 PRESTACAO (ANUL) - JUROS 515,00 EUR 0,00 EUR\n" + TEXT
    rows, warnings = parse_loan_history_text(text)
    assert warnings == [] and len(rows) == 6


@pytest.mark.asyncio
async def test_complete_printout_with_formalisation_keeps_all(session, tmp_path, monkeypatch):
    ext = await _process(session, tmp_path, monkeypatch, FORMAL + TEXT)
    assert ext.status == "needs_loan" and ext.error is None
    assert pe.json.loads(ext.payload_json) and len(pe.json.loads(ext.payload_json)) == 3


@pytest.mark.asyncio
async def test_oldest_instalment_number_1_counts_as_complete(session, tmp_path, monkeypatch):
    text = _inst(3, 12, "700,00") + _inst(2, 11, "800,00") + _inst(1, 10, "900,00")
    ext = await _process(session, tmp_path, monkeypatch, text)
    assert ext.error is None and len(pe.json.loads(ext.payload_json)) == 3


@pytest.mark.asyncio
async def test_cropped_printout_drops_oldest_and_says_so(session, tmp_path, monkeypatch):
    ext = await _process(session, tmp_path, monkeypatch, TEXT.replace(_inst(35, 10, "900,00"), CROPPED_TOP))
    assert ext.status == "needs_loan"
    assert "nº 35" in ext.error and "cropped" in ext.error
    assert len(pe.json.loads(ext.payload_json)) == 2  # trimmed payload


@pytest.mark.asyncio
async def test_cropped_printout_matches_loan_without_conflict(session, tmp_path, monkeypatch):
    first = await _process(session, tmp_path, monkeypatch, FORMAL + TEXT, n=1)
    debt = create_loan_from_assignment(session, "900100200", "Loan", "other")
    assign_history_to_loan(session, first, debt)
    full = {m.instalment_number: m.capital for m in session.exec(select(LoanMovement)).all()}
    cropped = TEXT.replace(_inst(35, 10, "900,00"), CROPPED_TOP)
    ext = await _process(session, tmp_path, monkeypatch, cropped, n=2)
    assert ext.status == "ok" and "cropped" in ext.error and "disagree" not in ext.error
    session.expire_all()
    assert {m.instalment_number: m.capital for m in session.exec(select(LoanMovement)).all()} == full


@pytest.mark.asyncio
async def test_single_instalment_is_not_trimmed(session, tmp_path, monkeypatch):
    ext = await _process(session, tmp_path, monkeypatch, _inst(37, 12, "700,00"))
    assert ext.error is None and len(pe.json.loads(ext.payload_json)) == 1
