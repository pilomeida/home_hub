"""PLAN step 5c: every check returns the same shape (verdict, doubtful items), and Health doubts reach the queue."""

import json
from datetime import date

import pytest

from app.categories.health import reading, store
from app.categories.health.extraction import ExtractedHealth
from app.categories.health.store import entry_for
from app.models.category import Category
from app.services import checks, review
from app.services.checks import DOUBTFUL, NOT_CHECKED, OK, CheckResult
from app.services.position_extraction import reconcile_loan
from app.services.statement_check import CheckLine, check_statement
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.knowledge_factories import make_document
from tests.test_health_extraction import _png


def test_the_statement_add_up_returns_the_shape():
    ok = check_statement([CheckLine(10.0, "debit"), CheckLine(5.0, "credit")], 100.0, 95.0).result()
    assert isinstance(ok, CheckResult) and ok.verdict == OK and ok.doubts == []
    bad = check_statement([CheckLine(10.0, "debit")], 100.0, 80.0).result()
    assert bad.verdict == DOUBTFUL and bad.doubts and all(d.message for d in bad.doubts)
    none = check_statement([CheckLine(10.0, "debit")], None, None).result()
    assert none.verdict == NOT_CHECKED


def test_positions_problems_return_the_shape():
    assert checks.from_problems(reconcile_loan([])).verdict == OK
    result = checks.from_problems(["loan 1: capital_remaining -5.00 is negative"])
    assert result.verdict == DOUBTFUL and result.notes() == ["loan 1: capital_remaining -5.00 is negative"]


def test_a_stored_card_statement_is_checked_ok():
    result = checks.card_statement_checked(12)
    assert result.verdict == OK and "to the cent" in result.summary


def test_notes_say_how_many_more_past_the_limit():
    result = checks.from_doubts([checks.Doubt(f"d{i}") for i in range(4)])
    assert result.notes(2) == ["d0", "d1", "... and 2 more. Look at the paper."]


@pytest.mark.asyncio
async def test_health_reading_carries_the_shape_and_a_scan_without_a_second_read_is_not_checked(tmp_path):
    reply = {"category": "lab_result", "person_name": "Ana", "document_date": "2026-01-01", "summary": "s",
             "layout": "sheet", "sections": [{"path": ["Bioquímica"], "results": [{"test": "Glucose", "value": 90,
                                                                                    "unit": "mg/dL"}]}]}
    gw = FakeGateway([gateway_text_result(json.dumps(reply))])
    got = await reading.read_health_document(_png(tmp_path), None, gateway=gw, second_read=False)
    assert got.check.verdict == NOT_CHECKED and got.check_notes == []
    gw = FakeGateway([gateway_text_result(json.dumps(reply)), gateway_text_result(json.dumps(reply))])
    got = await reading.read_health_document(_png(tmp_path), None, gateway=gw)
    assert got.check.verdict == OK


def test_a_health_paper_with_doubts_waits_for_a_person_and_looking_clears_it(session):
    doc = make_document(session, category=Category.HEALTH, paper_kind="lab_result", filename="lab.pdf")
    x = ExtractedHealth(person_name="Ana Lima", document_date=date(2026, 1, 1), summary="s",
                        check_notes=["Glucose: the value 91 is not printed on the paper."])
    store.apply_extraction(session, doc, x)
    items = [i for i in review.waiting(session, area="health") if i.kind == "health_doubts"]
    assert len(items) == 1 and "91 is not printed" in items[0].why and items[0].evidence_url.endswith(str(doc.id))
    review.decide(session, items[0].id, "approve", by="Rute")
    assert entry_for(session, doc.id).check_notes is None
    assert [i for i in review.waiting(session, area="health") if i.kind == "health_doubts"] == []


def test_clearing_the_notes_on_the_paper_closes_the_item(session):
    doc = make_document(session, category=Category.HEALTH, paper_kind="lab_result", filename="lab.pdf")
    store.apply_extraction(session, doc, ExtractedHealth(person_name="Ana Lima", document_date=date(2026, 1, 1),
                                                         summary="s", check_notes=["Something to look at."]))
    assert [i.kind for i in review.waiting(session, area="health")].count("health_doubts") == 1
    store.clear_notes(session, doc)
    assert [i.kind for i in review.waiting(session, area="health")].count("health_doubts") == 0
