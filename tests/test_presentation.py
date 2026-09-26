"""Unit tests for the shared presentation helpers (app/services/presentation.py):
turning a stored technical failure reason into plain English, humanizing an
internal claim key, and formatting dates/datetimes -- one mechanism per
concern, used everywhere the app currently prints raw technical text."""

from datetime import date, datetime

from app.services.presentation import (
    display_date,
    display_datetime,
    friendly_reason,
    humanize_key,
    humanize_reason_text,
)


def test_friendly_reason_maps_api_style_errors():
    raw = "Warranty date could not be read (Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error'}}) — enter it manually."
    rendered = str(friendly_reason(raw))
    assert "The AI service couldn't be reached." in rendered
    assert "<details>" in rendered and "<summary>Technical details</summary>" in rendered
    assert "authentication_error" in rendered  # kept, but only inside the technical details
    # the raw technical text must not appear outside the <details> block
    before_details = rendered.split("<details>")[0]
    assert "authentication_error" not in before_details


def test_friendly_reason_maps_invalid_pdf_errors():
    raw = "utility detail extraction failed: could not read this PDF"
    rendered = str(friendly_reason(raw))
    assert "The file couldn't be read." in rendered


def test_friendly_reason_maps_value_errors():
    raw = "'atm_withdrawal' is not a valid TransactionType"
    rendered = str(friendly_reason(raw))
    assert "Some details in this document couldn't be understood." in rendered


def test_friendly_reason_maps_unrecognized_technical_errors_to_generic_message():
    raw = "KeyError: 'amount'"
    rendered = str(friendly_reason(raw))
    assert "Something went wrong processing this document." in rendered


def test_friendly_reason_passes_through_plain_english_unchanged():
    raw = "duplicate — matched existing transaction"
    rendered = str(friendly_reason(raw))
    assert rendered == raw
    assert "<details>" not in rendered


def test_friendly_reason_appends_page_specific_actionable_sentence():
    raw = "Warranty date could not be read (Error code: 401 - {'type': 'error'})"
    rendered = str(friendly_reason(raw, "Enter it manually below."))
    assert "The AI service couldn't be reached. Enter it manually below." in rendered


def test_friendly_reason_empty_input_renders_empty():
    assert str(friendly_reason(None)) == ""
    assert str(friendly_reason("")) == ""


def test_friendly_reason_escapes_html_in_technical_detail():
    raw = "Error code: 401 - <script>alert(1)</script>"
    rendered = str(friendly_reason(raw))
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_humanize_reason_text_plain_summary_for_technical_reasons():
    assert humanize_reason_text("Error code: 401 - boom") == "The AI service couldn't be reached."
    assert humanize_reason_text("") == ""
    assert humanize_reason_text(None) == ""


def test_humanize_reason_text_passes_through_plain_english():
    assert humanize_reason_text("duplicate — matched existing transaction") == "duplicate — matched existing transaction"


def test_humanize_key_converts_snake_case_to_sentence_case():
    assert humanize_key("statement_period") == "Statement period"
    assert humanize_key("provider") == "Provider"
    assert humanize_key("") == ""


def test_display_date_formats_like_house_pages():
    assert display_date(date(2026, 8, 18)) == "18 Aug 2026"
    assert display_date(datetime(2026, 8, 18, 9, 5, 52, 222395)) == "18 Aug 2026"
    assert display_date(None) == "—"


def test_display_datetime_formats_day_month_year_comma_time():
    assert display_datetime(datetime(2026, 8, 18, 9, 5, 52, 222395)) == "18 Aug 2026, 09:05"
    assert display_datetime(None) == "—"
