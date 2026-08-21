import io
import json
from datetime import date, timedelta

import app.services.pipeline as pipeline_module
import app.services.wiki_engine as wiki_engine_module
from app.services.extraction import ExtractedBill, ExtractedStatement, ExtractedTransaction, ExtractedUtilityDetail


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text

    async def create(self, **kwargs):
        return _FakeMessage(self._response_text)


class _FakeAnthropicClient:
    def __init__(self, response_text):
        self.messages = _FakeMessages(response_text)


async def _fake_classify_bill(file_path, client=None):
    return "bill"


async def _fake_classify_statement(file_path, client=None):
    return "statement"


async def _noop_classify_transaction(session, transaction, client=None):
    return None


def _last_month_period_and_date(today: date = None) -> tuple[str, date]:
    """Mirror app.services.dashboard_service.get_dashboard_data's "last month"
    computation, so this test's notion of "last month" can never drift from
    the production code's notion of "last month"."""
    today = today or date.today()
    last_month_date = date(today.year, today.month, 1) - timedelta(days=1)
    last_period = f"{last_month_date.year:04d}-{last_month_date.month:02d}"
    return last_period, last_month_date


def _this_month_period(today: date = None) -> str:
    """Mirror app.services.dashboard_service.get_dashboard_data's "this month"
    computation, so this test's notion of "this month" can never drift from
    the production code's notion of "this month"."""
    today = today or date.today()
    return f"{today.year:04d}-{today.month:02d}"


def _next_month_date(today: date = None, day: int = 5) -> date:
    """A date in the month following `today` — used as a plausible bill due
    date that stays meaningful regardless of when the suite runs."""
    today = today or date.today()
    if today.month == 12:
        return date(today.year + 1, 1, day)
    return date(today.year, today.month + 1, day)


def test_full_bill_ingestion_flow(client, monkeypatch):
    wiki_response = json.dumps({
        "wiki_worthy": True,
        "topic": "Electricity — provider & contract",
        "facts": {"provider": "EDP"},
    })

    # Target "this month" (relative to whenever the suite runs), matching the
    # dashboard assertion below, which needs "electricity" to land in
    # spend_this_month — so this test never breaks on a calendar rollover.
    this_period = _this_month_period()
    due_date = _next_month_date()

    async def fake_extract_bill(file_path, client=None):
        return ExtractedBill(
            provider="EDP", category_hint="electricity", amount=87.32, currency="EUR",
            due_date=due_date, paid_date=None, statement_period=this_period,
        )

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return await wiki_engine_module.assess_and_update_wiki(
            session, document, transaction, client=_FakeAnthropicClient(wiki_response)
        )

    monkeypatch.setattr(pipeline_module, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline_module, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline_module, "assess_and_update_wiki", fake_assess_and_update_wiki)
    monkeypatch.setattr(pipeline_module, "classify_transaction", _noop_classify_transaction)

    upload_response = client.post(
        "/bills/upload",
        files={"file": ("edp-august.pdf", io.BytesIO(b"fake-pdf-bytes"), "application/pdf")},
        follow_redirects=False,
    )
    assert upload_response.status_code == 303

    dashboard_response = client.get("/")
    assert dashboard_response.status_code == 200
    assert "electricity" in dashboard_response.text.lower()
    assert "EDP" in dashboard_response.text

    todos_response = client.get("/todos")
    assert "EDP" in todos_response.text

    wiki_list_response = client.get("/wiki")
    assert "Electricity" in wiki_list_response.text


def test_full_statement_ingestion_flow(client, monkeypatch):
    # Target "last month" (relative to whenever the suite runs), matching
    # get_dashboard_data's own this-month/last-month computation, so this
    # test never breaks on a calendar rollover.
    last_period, last_month_date = _last_month_period_and_date()

    async def fake_extract_statement_transactions(file_path, client=None):
        return ExtractedStatement(
            statement_period=last_period,
            transactions=[
                ExtractedTransaction(
                    transaction_date=last_month_date, description="CONTINENTE MAFRA",
                    amount=42.15, currency="EUR", transaction_type="debit", category_hint="groceries",
                ),
                ExtractedTransaction(
                    transaction_date=last_month_date, description="SALARIO EMPRESA X",
                    amount=2200.0, currency="EUR", transaction_type="credit", category_hint="income",
                ),
            ],
        )

    monkeypatch.setattr(pipeline_module, "classify_document", _fake_classify_statement)
    monkeypatch.setattr(pipeline_module, "extract_statement_transactions", fake_extract_statement_transactions)
    monkeypatch.setattr(pipeline_module, "classify_transaction", _noop_classify_transaction)

    upload_response = client.post(
        "/bills/upload",
        files={"file": ("santander-july.pdf", io.BytesIO(b"fake-pdf-bytes"), "application/pdf")},
        follow_redirects=False,
    )
    assert upload_response.status_code == 303
    document_url = upload_response.headers["location"]

    detail_response = client.get(document_url)
    assert detail_response.status_code == 200
    assert "CONTINENTE MAFRA" in detail_response.text
    assert "SALARIO EMPRESA X" in detail_response.text

    dashboard_response = client.get("/")
    assert dashboard_response.status_code == 200
    assert "groceries" in dashboard_response.text.lower()
    # The credit (income) line must not appear in the spend-this-month section total —
    # 2200.0 would be an unmistakable outlier if it leaked into spend.
    assert "2200.00" not in dashboard_response.text

    todos_response = client.get("/todos")
    assert "CONTINENTE" not in todos_response.text
    assert "SALARIO" not in todos_response.text

    wiki_list_response = client.get("/wiki")
    assert "No wiki pages yet." in wiki_list_response.text


def test_electricity_bill_upload_appears_in_utilities_tab(client, monkeypatch):
    async def fake_classify_bill(file_path, client=None):
        return "bill"

    async def fake_extract_bill(file_path, client=None):
        return ExtractedBill(
            provider="EDP", category_hint="electricity", amount=85.0, currency="EUR",
            due_date=None, paid_date=None, statement_period="2026-07",
        )

    async def fake_extract_utility_detail(file_path, utility_type, client=None):
        return ExtractedUtilityDetail(
            period_label="2026-07", billing_period_start=None, billing_period_end=None,
            invoice_number="FA CO26/42 105", consumption_value=401.0, consumption_unit="kWh",
            energy_cost=56.5, power_cost=4.54, fees_taxes_cost=12.99, vat_cost=10.97,
        )

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return None

    monkeypatch.setattr(pipeline_module, "classify_document", fake_classify_bill)
    monkeypatch.setattr(pipeline_module, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline_module, "extract_utility_detail", fake_extract_utility_detail)
    monkeypatch.setattr(pipeline_module, "assess_and_update_wiki", fake_assess_and_update_wiki)
    monkeypatch.setattr(pipeline_module, "classify_transaction", _noop_classify_transaction)

    upload_response = client.post(
        "/bills/upload",
        files={"file": ("edp-july.pdf", io.BytesIO(b"fake-pdf-bytes"), "application/pdf")},
        follow_redirects=False,
    )
    assert upload_response.status_code == 303

    electricity_response = client.get("/utilities/electricity")
    assert electricity_response.status_code == 200
    assert "2026-07" in electricity_response.text
    assert "401" in electricity_response.text

    dashboard_response = client.get("/")
    assert dashboard_response.status_code == 200
    assert "electricity" in dashboard_response.text.lower()
