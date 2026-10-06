"""Deterministic parser for `pdftotext -raw` output of a loan-history printout.
SYNTHETIC text only, shaped like the real layout."""
import asyncio
import json
from datetime import date

import pytest

from app.models.document import Document, DocumentSource, DocumentStatus
from app.services import position_extraction as pe
from app.services.position_extraction import (
    PositionExtractionError, aggregate_components, extract_loan_history, parse_loan_history_text, reconcile_loan,
)
from app.services.position_store import process_loan_history_document
from tests.fakes.fake_gateway import FakeGateway

HEADER = "Consulta Movimentos\nData Mov. Data Valor N Descricao Valor Saldo\n"
FOOTER = "Pagina 1 de 1\nAviso: documento sem valor legal\n"


def _l(d, n, comp, amount, bal):
    return f"{d} {d} {n} PRESTACAO - {comp} -{amount} EUR {bal} EUR\n"


TEXT = (HEADER
        + _l("02-10-2026", 38, "SEG ED", "5,10", "0,00")
        + _l("02-10-2026", 38, "SEGURO", "7,20", "0,00")
        + _l("02-10-2026", 38, "JUROS", "211,40", "0,00")
        + _l("02-10-2026", 38, "CAPIT.", "287,38", "113.311,81")
        + _l("02-09-2026", 37, "SEG ED", "5,10", "0,00")
        + _l("02-09-2026", 37, "SEGURO", "7,20", "0,00")
        + _l("02-09-2026", 37, "JUROS", "212,00", "0,00")
        + _l("02-09-2026", 37, "CAPIT.", "286,80", "113.599,19")
        + FOOTER)


def test_parses_all_four_components_and_reconciles():
    rows, warnings = parse_loan_history_text(TEXT)
    assert warnings == [] and len(rows) == 8
    assert {r.component for r in rows} == {"capital", "interest", "insurance_life", "insurance_building"}
    assert all(r.amount > 0 for r in rows)
    assert reconcile_loan(rows) == []
    insts = aggregate_components(rows)
    assert [i.number for i in insts] == [37, 38]
    assert insts[1].capital == 287.38 and insts[1].interest == 211.40 and insts[1].insurance == 12.30
    assert insts[1].balance_after == 113311.81 and insts[0].date == date(2026, 9, 2)


def test_balance_only_on_capital_rows():
    rows, _ = parse_loan_history_text(TEXT)
    assert all((r.balance_after is None) == (r.component != "capital") for r in rows)


def test_partial_payment_three_capital_rows_is_one_instalment():
    text = (_l("02-10-2026", 40, "JUROS", "200,00", "0,00")
            + _l("02-10-2026", 40, "CAPIT.", "100,00", "5.000,00")
            + _l("03-10-2026", 40, "CAPIT.", "50,00", "4.950,00")
            + _l("04-10-2026", 40, "CAPIT.", "25,00", "4.925,00"))
    rows, _ = parse_loan_history_text(text)
    insts = aggregate_components(rows)
    assert len(insts) == 1 and insts[0].capital == 175.0 and insts[0].balance_after == 4925.0


def test_interest_only_series_has_no_balance():
    text = _l("02-08-2026", 10, "JUROS", "90,00", "0,00") + _l("02-09-2026", 11, "JUROS", "91,00", "0,00")
    rows, warnings = parse_loan_history_text(text)
    assert len(rows) == 2 and all(r.balance_after is None for r in rows) and warnings == []


def test_final_payoff_row_keeps_zero_only_when_newest():
    text = (_l("02-08-2026", 49, "JUROS", "1,00", "0,00") + _l("02-08-2026", 49, "CAPIT.", "100,00", "0,00"))
    rows, _ = parse_loan_history_text(text)
    assert [r.balance_after for r in rows if r.component == "capital"] == [0.0]
    older = (text + _l("02-09-2026", 50, "JUROS", "1,00", "0,00") + _l("02-09-2026", 50, "CAPIT.", "100,00", "900,00"))
    rows, _ = parse_loan_history_text(older)
    assert [r.balance_after for r in rows if r.component == "capital" and r.instalment_number == 49] == [None]


def test_unknown_component_is_skipped_with_warning():
    text = _l("02-10-2026", 38, "COMISSAO", "3,00", "0,00") + _l("02-10-2026", 38, "JUROS", "9,00", "0,00")
    rows, warnings = parse_loan_history_text(text)
    assert len(rows) == 1 and any("COMISSAO" in w for w in warnings)


def test_garbage_ignored_and_bad_candidate_counted():
    text = "random line\n12 PRESTACAO\n" + _l("02-10-2026", "x", "JUROS", "9,00", "0,00") + _l("02-10-2026", 3, "JUROS", "9,00", "0,00")
    rows, warnings = parse_loan_history_text(text)
    assert len(rows) == 1 and any("1 line" in w for w in warnings)
    assert parse_loan_history_text("nothing here")[0] == []


# ---- the extraction wiring ---------------------------------------------------

def _pdf(tmp_path, body=b"%PDF-1.4 fake"):
    p = tmp_path / "doc.pdf"
    p.write_bytes(body)
    return str(p)


def _llm_reply():
    rows = [{"date": "2026-01-10", "instalment_number": 1, "component": "capital", "amount": 100.0, "balance_after": 900.0},
            {"date": "2026-01-10", "instalment_number": 1, "component": "interest", "amount": 20.0, "balance_after": None}]
    return {"text": json.dumps({"rows": rows}), "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_deterministic_path_makes_no_gateway_call(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "_pdf_text", lambda path: TEXT)
    fake = FakeGateway([])
    hist = asyncio.run(extract_loan_history(_pdf(tmp_path), gateway=fake))
    assert fake.requests == [] and len(hist.rows) == 8 and hist.warnings == []


def test_zero_matched_rows_falls_back_to_gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "_pdf_text", lambda path: "no table here")
    fake = FakeGateway([_llm_reply()])
    hist = asyncio.run(extract_loan_history(_pdf(tmp_path), gateway=fake))
    assert len(fake.requests) == 1 and len(hist.rows) == 2


def test_pdftotext_missing_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(pe.shutil, "which", lambda name: None)
    fake = FakeGateway([_llm_reply()])
    asyncio.run(extract_loan_history(_pdf(tmp_path), gateway=fake))
    assert len(fake.requests) == 1


def test_use_text_parser_false_forces_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "_pdf_text", lambda path: TEXT)
    fake = FakeGateway([_llm_reply()])
    asyncio.run(extract_loan_history(_pdf(tmp_path), gateway=fake, use_text_parser=False))
    assert len(fake.requests) == 1


def test_parse_warnings_are_carried(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "_pdf_text", lambda path: TEXT + _l("02-08-2026", "x", "JUROS", "9,00", "0,00"))
    hist = asyncio.run(extract_loan_history(_pdf(tmp_path), gateway=FakeGateway([])))
    assert any("skipped" in w or "line" in w for w in hist.warnings)


def test_llm_row_without_instalment_number_has_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "_pdf_text", lambda path: None)
    rows = [{"date": "2026-01-10", "instalment_number": None, "component": "capital", "amount": 1.0, "balance_after": 1.0}]
    reply = {"text": json.dumps({"rows": rows}), "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}
    with pytest.raises(PositionExtractionError, match="row 1 has no instalment number"):
        asyncio.run(extract_loan_history(_pdf(tmp_path), gateway=FakeGateway([reply])))


@pytest.mark.asyncio
async def test_wrapped_bytes_end_to_end_through_the_store(session, tmp_path, monkeypatch):
    from app.services.pdf_bytes import normalize_pdf_bytes
    clean = normalize_pdf_bytes(b"\xac\xed\x00\x05ur\x00\x02[B".ljust(27, b"\x00") + b"%PDF-1.7 x %%EOF\n")
    path = tmp_path / "w.pdf"
    path.write_bytes(clean)
    doc = Document(filename="w.pdf", file_path=str(path), content_hash="wrap-e2e", source=DocumentSource.MANUAL,
                   status=DocumentStatus.PENDING, category="loan_history")
    session.add(doc)
    session.commit()
    session.refresh(doc)
    monkeypatch.setattr(pe, "_pdf_text", lambda p: TEXT)
    fake = FakeGateway([])
    ext = await process_loan_history_document(session, doc, gateway=fake)
    assert fake.requests == [] and ext.status == "needs_loan"
