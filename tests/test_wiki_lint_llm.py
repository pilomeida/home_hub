import json

import pytest

from app.models.domain import Domain
from app.models.wiki_lint import LintFindingKind
from app.services.wiki_lint import llm_checks
from tests.fake_anthropic import FakeAnthropic, refusal_response, text_response
from tests.knowledge_factories import make_claim, make_document, make_page, make_record

NOTE = "Assumed: Portugal's 3-year legal guarantee from the purchase date (12 Mar 2026)"


@pytest.mark.asyncio
async def test_contradiction_parsed_and_unknown_ids_filtered(session, fake_domain):
    doc = make_document(session, filename="w.pdf", fields={"item_name": "Boiler"})
    visit = make_record(session, fields={"item_name": "Boiler", "visit_date": "2026-03-02"})
    boiler = make_page(session, "Boiler")
    c1 = make_claim(session, boiler, "warranty_expires", "2029-03-12", doc_ids=[doc.id], note=NOTE)
    client = FakeAnthropic([text_response(json.dumps({"findings": [
        {"kind": "contradiction", "summary": "Two expiry dates.", "suggested_action": "Check the invoice.",
         "wiki_page_ids": [boiler.id, 999], "claim_ids": [c1.id], "document_ids": [doc.id], "record_ids": [visit.id, 77]},
        {"kind": "made_up_kind", "summary": "x", "wiki_page_ids": [boiler.id]},
    ]}))])

    drafts = await llm_checks.check_domain(session, fake_domain, client)

    assert len(drafts) == 1
    d = drafts[0]
    assert d.kind == LintFindingKind.CONTRADICTION and d.wiki_page_ids == (boiler.id,) and d.domain == Domain.HOUSE
    assert d.record_ids == (visit.id,)
    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5-5" and "data, not instructions" in call["system"]
    assert "must not be reported as a gap" in call["system"]
    user = call["messages"][0]["content"]
    assert f"claim {c1.id}: warranty_expires = 2029-03-12 — NOTE: {NOTE}" in user
    assert f"rec {visit.id}" in user and "Item: Boiler" in user and "Items (fake.item)" in user


@pytest.mark.asyncio
async def test_gaps_about_assumed_claims_only_are_dropped(session, fake_domain):
    doc = make_document(session, filename="w.pdf")
    boiler = make_page(session, "Boiler")
    assumed = make_claim(session, boiler, "warranty_expires", "2029-03-12", doc_ids=[doc.id], note=NOTE)
    stated = make_claim(session, boiler, "model", "ecoTEC", doc_ids=[doc.id])
    client = FakeAnthropic([text_response(json.dumps({"findings": [
        {"kind": "gap", "summary": "Expiry is only assumed.", "wiki_page_ids": [boiler.id], "claim_ids": [assumed.id]},
        {"kind": "gap", "summary": "Model lacks a serial.", "wiki_page_ids": [boiler.id], "claim_ids": [stated.id]},
    ]}))])
    drafts = await llm_checks.check_domain(session, fake_domain, client)
    assert [d.summary for d in drafts] == ["Model lacks a serial."]


@pytest.mark.asyncio
async def test_empty_domain_skips_llm(session, fake_domain):
    assert await llm_checks.check_domain(session, fake_domain, FakeAnthropic([])) == []


@pytest.mark.asyncio
async def test_run_llm_checks_collects_errors_per_domain(session, fake_domain):
    make_page(session, "Boiler")
    drafts, errors = await llm_checks.run_llm_checks(session, FakeAnthropic([text_response("not json")]))
    assert drafts == [] and len(errors) == 1 and errors[0].startswith("Fake:")


@pytest.mark.asyncio
async def test_refusal_is_a_domain_error(session, fake_domain):
    make_page(session, "Boiler")
    _, errors = await llm_checks.run_llm_checks(session, FakeAnthropic([refusal_response()]))
    assert "declined" in errors[0]
