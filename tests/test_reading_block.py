"""PLAN step 5a: the shared reading block (one error type, Portuguese numbers and dates, a size guard, one JSON read)."""

import asyncio
import json
from datetime import date

import pytest

from app.categories.health.extraction import HealthExtractionError, extract_health_document
from app.categories.house.warranty import WarrantyExtractionError
from app.categories.generic.reader import GenericReadingError, read_paper
from app.problems import classify_reason
from app.services import reading
from app.services.document_input import UnsupportedFileTypeError, build_content_block
from app.services.extraction import (
    ClassificationError, ExtractionError, StatementExtractionError, UtilityDetailExtractionError, extract_bill,
    extract_statement_transactions,
)
from app.services.position_extraction import PositionExtractionError
from app.services.reading import FileTooLargeError, ReadingError, day, json_object, number
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result

# These call readers directly; production calls them inside a privacy scope (tests/conftest.py).
pytestmark = pytest.mark.usefixtures("levels_not_binding")


@pytest.mark.parametrize("raw, expected", [
    (12.5, 12.5), ("12.5", 12.5), ("1.234,56", 1234.56), ("1,234.56", 1234.56), ("-12,30 €", -12.3),
    ("1 234,56", 1234.56), ("12,30-", -12.3), ("€ 12.345.678", 12345678.0), ("1.234 €", 1234.0), ("−3,5", -3.5),
    ("5,2", 5.2), (0, 0.0),
])
def test_numbers_as_papers_print_them(raw, expected):
    assert number(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [None, True, "abc", "nan", "inf", float("nan"), "< 5", "4,20 - 5,50", ""])
def test_what_is_no_number_is_not_printed(raw):
    assert number(raw) is None


@pytest.mark.parametrize("raw, expected", [
    ("2025-03-05", date(2025, 3, 5)), ("2025-03-05T10:00:00", date(2025, 3, 5)), ("05/03/2025", date(2025, 3, 5)),
    ("5-3-2025", date(2025, 3, 5)), ("05.03.25", date(2025, 3, 5)), (date(2024, 1, 2), date(2024, 1, 2)),
])
def test_dates_day_first(raw, expected):
    assert day(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "31/02/2025", "March 2025", "2025-13-01", True])
def test_what_is_no_date_is_none(raw):
    assert day(raw) is None


def test_every_reader_error_is_a_reading_error():
    for error in (ExtractionError, ClassificationError, StatementExtractionError, UtilityDetailExtractionError,
                  HealthExtractionError, GenericReadingError, WarrantyExtractionError, PositionExtractionError,
                  UnsupportedFileTypeError, FileTooLargeError):
        assert issubclass(error, ReadingError), error


def test_json_object_raises_the_readers_own_error():
    assert json_object('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(GenericReadingError):
        json_object("not json", error=GenericReadingError)
    with pytest.raises(GenericReadingError):
        json_object("[1, 2]", error=GenericReadingError)


def test_a_file_too_big_is_refused_before_upload_with_a_plain_reason(tmp_path, monkeypatch):
    pdf = tmp_path / "big.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"0" * 2048)
    monkeypatch.setattr(reading, "MAX_FILE_BYTES", 1024)
    monkeypatch.setattr(reading.check_size, "__defaults__", (1024,))
    gateway = FakeGateway([gateway_text_result(json.dumps({"provider": "EDP"}))])
    with pytest.raises(FileTooLargeError) as caught:
        asyncio.run(extract_bill(str(pdf), gateway=gateway))
    assert gateway.requests == []                                       # nothing was sent
    cause = classify_reason(str(caught.value))                       # what the family sees, from the stored text
    assert cause is not None and "too big" in cause.why


def test_an_unknown_file_type_is_refused_before_reading_it(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    with pytest.raises(UnsupportedFileTypeError):
        build_content_block(str(video))


def test_statement_lines_printed_the_portuguese_way_are_read(tmp_path):
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    answer = {"statement_period": "2025-03", "opening_balance": "1.234,56", "closing_balance": "1.200,00",
              "transactions": [{"date": "05/03/2025", "description": "LIDL", "amount": "34,56", "type": "debit"}]}
    statement = asyncio.run(extract_statement_transactions(str(pdf), gateway=FakeGateway([gateway_text_result(json.dumps(answer))])))
    assert statement.opening_balance == pytest.approx(1234.56)
    assert statement.transactions[0].transaction_date == date(2025, 3, 5)
    assert statement.transactions[0].amount == pytest.approx(34.56)


def test_a_statement_line_with_no_readable_amount_fails_the_reading(tmp_path):
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    answer = {"transactions": [{"date": "2025-03-05", "description": "X", "amount": "n/a", "type": "debit"}]}
    with pytest.raises(StatementExtractionError):
        asyncio.run(extract_statement_transactions(str(pdf), gateway=FakeGateway([gateway_text_result(json.dumps(answer))])))


def test_health_values_with_a_decimal_comma_are_kept(tmp_path):
    img = tmp_path / "lab.png"
    img.write_bytes(b"\x89PNG\r\n")
    answer = {"category": None, "sections": [{"path": ["Hematologia"], "results": [
        {"test": "Hemoglobina", "value": "13,4", "unit": "g/dL", "reference": "12,0 - 15,5", "measured_on": "05/03/2025"}]}]}
    read = asyncio.run(extract_health_document(str(img), None, gateway=FakeGateway([gateway_text_result(json.dumps(answer))])))
    row = read.measurements[0]
    assert row.value == pytest.approx(13.4) and row.measured_on == date(2025, 3, 5)


def test_a_long_other_paper_is_cut_and_the_reader_is_told(tmp_path):
    gateway = FakeGateway([gateway_text_result(json.dumps({"title": "T", "date": "01/02/2024", "about": "", "summary": "S"}))])
    out = asyncio.run(read_paper(str(tmp_path / "x.pdf"), "palavra " * 10_000, gateway=gateway))
    assert out.paper_date == date(2024, 2, 1)
    assert "only its beginning is given" in gateway.requests[0]["user"]
