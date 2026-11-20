"""PLAN step 9 D11: a statement too big to read whole is read in parts of a few pages; no page is read twice, line
pages are the paper's own, and a part that cannot be read names its pages."""

import asyncio
import base64
import json
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from app.models.category import Category
from app.privacy.scope import privacy_scope
from app.services.document_input import pdf_page_count, pdf_pages_block
from app.services.extraction import StatementExtractionError, extract_statement_in_parts
from app.services.reading import ReadingError
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result


def _pdf(tmp_path, pages=6):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    path = tmp_path / "statement.pdf"
    with open(path, "wb") as f:
        writer.write(f)
    return str(path)


def _read(session, *args, **kwargs):
    with privacy_scope(session, Category.FINANCIALS, "statement"):        # as the script reads it
        return asyncio.run(extract_statement_in_parts(*args, **kwargs))


def _pages_sent(request) -> int:
    data = request["attachments"][0]["source"]["data"]
    return len(PdfReader(BytesIO(base64.b64decode(data))).pages)


def _part(lines, opening=None, closing=None):
    return gateway_text_result(json.dumps({
        "statement_period": "2022-08", "opening_balance": opening, "closing_balance": closing,
        "transactions": [{"date": d, "description": t, "amount": a, "type": "debit", "page": p, "balance_after": b}
                         for d, t, a, p, b in lines]}))


def test_a_page_range_is_sent_as_its_own_pdf(tmp_path):
    path = _pdf(tmp_path)
    assert pdf_page_count(path) == 6
    block = pdf_pages_block(path, 2, 4)
    assert len(PdfReader(BytesIO(base64.b64decode(block["source"]["data"]))).pages) == 3
    with pytest.raises(ReadingError):
        pdf_pages_block(path, 5, 9)


def test_parts_never_share_a_page_and_lines_keep_the_papers_pages(session, tmp_path):
    path = _pdf(tmp_path)
    gw = FakeGateway([
        _part([("2022-08-02", "Shop A", 10.0, 1, 90.0), ("2022-08-20", "Shop B", 5.0, 4, 85.0)], opening=100.0, closing=85.0),
        _part([("2022-09-01", "Shop C", 20.0, 2, 65.0)], opening=85.0, closing=65.0),
    ])
    read = _read(session, path, 1, 6, pages_per_part=4, gateway=gw)
    assert [_pages_sent(r) for r in gw.requests] == [4, 2]
    assert [(t.description, t.page) for t in read.transactions] == [("Shop A", 1), ("Shop B", 4), ("Shop C", 6)]
    assert (read.opening_balance, read.closing_balance) == (100.0, 65.0)


def test_a_part_that_cannot_be_read_names_its_pages(session, tmp_path):
    path = _pdf(tmp_path)
    gw = FakeGateway([_part([("2022-08-02", "Shop A", 10.0, 1, 90.0)]), gateway_text_result("not json")])
    with pytest.raises(StatementExtractionError, match="pages 5-6"):
        _read(session, path, 1, 6, pages_per_part=4, gateway=gw)


def test_the_running_balance_settles_a_line_the_reader_called_the_wrong_way(session, tmp_path):
    """Document 51: the reader called "Transfer from MATIAS" money out while the printed balance rose by its amount."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("read_statement_part", "scripts/read_statement_part.py")
    script = importlib.util.module_from_spec(spec); spec.loader.exec_module(script)
    path = _pdf(tmp_path, pages=2)
    gw = FakeGateway([_part([("2022-09-08", "Card payment", 10.0, 1, 26.71),
                             ("2022-09-08", "Transfer from MATIAS ALMEIDA", 2.0, 1, 28.71),     # read as a debit
                             ("2022-09-09", "Unreadable balance", 5.0, 2, 99.0)])])             # proves nothing
    lines = _read(session, path, 1, 2, pages_per_part=4, gateway=gw).transactions
    assert script._settle_directions(lines) == 1
    assert [t.transaction_type for t in lines] == ["debit", "credit", "debit"]
    assert len(script._balance_breaks(lines)) == 1                      # the unproven line is still reported
