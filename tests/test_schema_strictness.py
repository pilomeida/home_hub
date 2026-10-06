"""Schema-strictness audit: every response_schema must be no stricter than
its prompt + parser. Each test feeds a null-heavy but parser-valid response
through jsonschema.validate — the schema must accept what the parser would
happily default."""

import jsonschema

from app.domains.house import warranty
from app.services import classification_engine, domain_classifier, wiki_engine
from app.services import extraction
from app.services.wiki_lint import llm_checks


def _validate(schema, instance):
    jsonschema.validate(instance=instance, schema=schema)


# --- _BILL_SCHEMA: prompt says "use null", parser defaults ---------------


def test_bill_schema_allows_nulls_where_the_parser_defaults():
    # Parser: category_hint→other, amount→0.0, currency→EUR, dates → None.
    _validate(extraction._BILL_SCHEMA, {
        "provider": "EDP",
        "category_hint": None,
        "amount": None,
        "currency": None,
        "due_date": None,
        "paid_date": None,
        "statement_period": None,
    })


def test_bill_schema_does_not_require_parser_defaulted_keys():
    _validate(extraction._BILL_SCHEMA, {"provider": "EDP"})


# --- _CLASSIFICATION_SCHEMA: parser indexes document_type directly -------


def test_classification_schema_keeps_required_key_the_parser_indexes():
    _validate(extraction._CLASSIFICATION_SCHEMA, {"document_type": "bill"})


# --- _STATEMENT_SCHEMA: currency/category_hint defaulted by parser -------


def test_statement_schema_allows_null_currency_and_category_hint():
    _validate(extraction._STATEMENT_SCHEMA, {
        "statement_period": None,
        "transactions": [{
            "date": "2026-01-02",
            "description": "Modelo Hiper",
            "amount": 12.5,
            "currency": None,
            "type": "debit",
            "category_hint": None,
        }],
    })


def test_statement_schema_keeps_required_keys_the_parser_indexes():
    # date/description/amount/type are indexed directly (item["date"] etc.);
    # transactions is indexed directly (data["transactions"]).
    schema = extraction._STATEMENT_SCHEMA
    assert schema["required"] == ["transactions"]
    item_schema = schema["properties"]["transactions"]["items"]
    assert set(item_schema["required"]) == {"date", "description", "amount", "type"}


# --- _UTILITY_SCHEMA: already null-permissive ----------------------------


def test_utility_schema_allows_nulls_everywhere_but_period_label():
    _validate(extraction._UTILITY_SCHEMA, {
        "period_label": "2026-08",
        "billing_period_start": None,
        "billing_period_end": None,
        "invoice_number": None,
        "consumption_value": None,
        "consumption_unit": None,
        "energy_cost": None,
        "power_cost": None,
        "fees_taxes_cost": None,
        "vat_cost": None,
    })


# --- _WARRANTY_SCHEMA: keys the parser indexes directly, values nullable --


def test_warranty_schema_allows_null_dates():
    _validate(warranty._WARRANTY_SCHEMA, {"expiry_date": None, "purchase_date": None})


# --- _FINDINGS_SCHEMA: parser .gets everything ---------------------------


def test_findings_schema_allows_null_and_missing_optional_fields():
    _validate(llm_checks._FINDINGS_SCHEMA, {
        "findings": [{
            "kind": "gap",
            "summary": None,
            "suggested_action": None,
        }],
    })
    _validate(llm_checks._FINDINGS_SCHEMA, {"findings": [{}]})


# --- _DOMAIN_SCHEMA: parser .gets and defaults everything ----------------


def test_domain_schema_allows_null_and_missing_fields():
    _validate(domain_classifier._DOMAIN_SCHEMA, {
        "domain": None, "category": None, "confidence": None, "reason": None,
    })
    _validate(domain_classifier._DOMAIN_SCHEMA, {})


# --- _WIKI_SCHEMA / _MERCHANT_SCHEMA: parser indexes these directly ------


def test_wiki_and_merchant_schemas_stay_strict_where_the_parser_indexes(session):
    _validate(wiki_engine._WIKI_SCHEMA, {"pages": [{"title": "T", "summary": "S", "facts": {}}]})
    from app.services.taxonomy import ensure_taxonomy, leaf_slugs

    ensure_taxonomy(session)
    slugs = leaf_slugs(session)
    schema = classification_engine._merchant_schema(slugs)
    assert set(schema["required"]) == {"canonical_name", "node_slug", "nature"}
    assert schema["properties"]["node_slug"]["enum"] == slugs
    _validate(schema, {
        "canonical_name": "Modelo Hiper", "node_slug": slugs[0], "nature": "essential",
    })


# --- end-to-end: a null-heavy bill still parses with defaults ------------


def test_null_heavy_bill_response_parses_with_parser_defaults(tmp_path):
    import asyncio

    from tests.fakes.fake_gateway import FakeGateway

    pdf = tmp_path / "bill.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    fake = FakeGateway([{
        "text": '{"provider": "EDP", "category_hint": null, "amount": null, '
                '"currency": null, "due_date": null, "paid_date": null, "statement_period": null}',
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }])
    bill = asyncio.run(extraction.extract_bill(str(pdf), gateway=fake))
    assert bill.currency == "EUR"
    assert bill.amount == 0.0
    # category_hint: `data.get("category_hint", "other")` returns the null
    # that IS present; normalize_category(None) downstream maps to OTHER.
    assert bill.category_hint is None

# --- position extraction schemas: parser indexes only as_of / rows --------


def test_positions_schema_accepts_null_heavy_reply():
    from app.services import position_extraction

    _validate(position_extraction._POSITIONS_SCHEMA, {
        "as_of": "2026-07-31",
        "loans": [{"number": "1", "capital_remaining": 1.0}],
        "funds": None,
        "balances": None,
    })
    _validate(position_extraction._POSITIONS_SCHEMA, {"as_of": "2026-07-31"})


def test_loan_history_schema_accepts_minimal_rows():
    from app.services import position_extraction

    _validate(position_extraction._LOAN_HISTORY_SCHEMA, {
        "rows": [{"date": "2026-10-02", "instalment_number": 38, "component": "capital", "amount": 1.0}],
    })


def test_positions_schema_accepts_null_indexante_and_spread():
    from app.services import position_extraction

    loan = {"number": "1", "capital_remaining": 1.0, "next_indexante_percent": None, "next_spread_percent": None}
    _validate(position_extraction._POSITIONS_SCHEMA, {"as_of": "2026-07-31", "loans": [loan]})
    loan.update(next_indexante_percent=1.8, next_spread_percent="1,2")
    _validate(position_extraction._POSITIONS_SCHEMA, {"as_of": "2026-07-31", "loans": [loan]})
