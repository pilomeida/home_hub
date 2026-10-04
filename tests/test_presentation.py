"""Unit tests for the shared presentation helpers (app/services/presentation.py):
turning a stored technical failure reason into plain English, humanizing an
internal claim key, and formatting dates/datetimes -- one mechanism per
concern, used everywhere the app currently prints raw technical text."""

from datetime import date, datetime

from app.llm_gateway import GatewayError
from app.services.presentation import (
    display_date,
    display_datetime,
    claim_label,
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


def test_friendly_reason_invalid_pdf_api_error_is_a_file_problem_not_an_outage():
    # The API rejects a broken PDF with a 400 whose message names the PDF:
    # that's the file's fault, not an unreachable AI service.
    raw = ("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
           "'message': 'messages.0.content.0.pdf.source.base64.data: The PDF specified was not valid.'}}")
    assert humanize_reason_text(raw) == "The file couldn't be read."


def test_friendly_reason_maps_gateway_outage_errors():
    # GatewayError detail from a 5xx / transport failure: temporary outage.
    # Built from real GatewayError instances, exactly as ingestion stores
    # str(exc) -- never hand-typed, so the test cannot drift from production.
    for raw in (
        str(GatewayError(503, "{'detail': 'all suppliers failing'}")),
        str(GatewayError(0, "gateway unreachable at http://127.0.0.1:8010/run: connection refused")),
        str(GatewayError(529, "overloaded")),
        str(GatewayError(408, "request timeout")),
    ):
        assert humanize_reason_text(raw) == "The AI service couldn't be reached.", raw


def test_friendly_reason_maps_gateway_refusal_errors_to_couldnt_be_read():
    # 4xx from the gateway (no model paired, request too large, bad file):
    # a plain "couldn't be read automatically" message, not an outage.
    for raw in (
        str(GatewayError(422, "{'detail': 'no model paired for (hub, vision_extraction)'}")),
        str(GatewayError(413, "{'detail': 'request too large'}")),
        str(GatewayError(400, "{'detail': 'invalid base64 in attachments'}")),
        str(GatewayError(403, "{'detail': 'bad worker token'}")),
    ):
        assert humanize_reason_text(raw) == "This document couldn't be read automatically.", raw


def test_friendly_reason_invalid_pdf_gateway_error_is_a_file_problem_not_an_outage():
    # Same ordering rule as before: if the error text names the PDF, the
    # file is at fault, even though the gateway wrapped it in a 4xx.
    raw = str(GatewayError(400, "{'detail': 'The PDF specified was not valid.'}"))
    assert humanize_reason_text(raw) == "The file couldn't be read."


def test_claim_label_prefers_a_real_label_and_humanizes_missing_or_key_echo_labels():
    assert claim_label("Warranty expires", "warranty_expiry") == "Warranty expires"
    assert claim_label(None, "statement_period") == "Statement period"
    # legacy claims were backfilled with label == key
    assert claim_label("statement_period", "statement_period") == "Statement period"
