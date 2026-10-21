"""Filing a health paper end to end through the real ingestion core: privacy scope High, extraction,
tables, wiki, status. Wire = the real GatewayClient over a MockTransport."""

import json

import pytest
from sqlmodel import select

from app.domains.health import store
from app.llm_gateway import set_gateway_provider
from app.models.document import DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.health import HealthEntry, HealthMeasurement
from app.models.wiki import WikiClaim
from app.services.ingestion import Classification, IncomingFile, ingest
from tests import privacy_world as pw
from tests.fakes.fake_gateway import gateway_text_result
from tests.test_health_extraction import REPLY

WIKI_REPLY = {"pages": [{"title": "Maria: cholesterol", "summary": "LDL above range in March 2026",
                         "facts": {"ldl_2026_03": "162 mg/dL"}}]}


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "documents")
    yield
    set_gateway_provider(None)


async def _file(session, wire, content=b"\x89PNG\r\n\x1a\nlab", name="lab.png", reply=REPLY):
    # a photo has no text layer, so it is read twice (the second reading is compared with the first), then the wiki
    wire.script = [gateway_text_result(json.dumps(reply)), gateway_text_result(json.dumps(reply)), gateway_text_result(json.dumps(WIKI_REPLY))]
    set_gateway_provider(lambda: wire.client())
    return await ingest(session, IncomingFile(name, content, DocumentSource.MANUAL, "test"),
                        Classification(Domain.HEALTH, "lab_result", {}))


@pytest.mark.asyncio
async def test_a_lab_result_is_read_stored_tagged_and_in_the_wiki_all_at_high(session):
    pw.set_levels(session, on=False)                    # Health must not depend on the switch
    wire = pw.Wire()
    result = await _file(session, wire)
    doc = result.document
    assert doc.status == DocumentStatus.PROCESSED and doc.sensitivity_tier == "house_high"
    assert [b["sensitivity"] for b in wire.parsed()] == ["house_high"] * 3    # two readings, wiki

    entry = store.entry_for(session, doc.id)
    assert entry.provider == "Laboratório Norte" and entry.year == 2026 and entry.kind == "test"
    assert entry.person_id is not None and entry.amount_billed == 45.5
    ms = session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)).all()
    assert {m.test for m in ms} == {"LDL cholesterol", "Glucose"}
    ldl = next(m for m in ms if m.test_key == "ldl cholesterol mg/dl")      # the key carries the unit
    assert ldl.out_of_range is True and ldl.needs_check is False
    assert next(m for m in ms if m.test == "Glucose").needs_check is True
    assert entry.needs_check is True                                              # because one value is doubtful
    assert session.exec(select(WikiClaim)).all()                                  # something reached the wiki


@pytest.mark.asyncio
async def test_every_printed_data_point_is_kept_limits_words_history_and_the_other_details(session):
    reply = dict(REPLY, details="NIF: 123456789\nRequisitada por: Dra. Ana Silva\nData de saída: 2024-11-11")
    reply["measurements"] = REPLY["measurements"] + [
        {"test": "Microalbuminúria", "value": None, "value_text": "<3", "unit": "mg/L", "ref_low": None, "ref_high": 20,
         "measured_on": "2024-11-11", "uncertain": False, "source_text": "Microalbuminúria <3 mg/L (<20)"},
        {"test": "Nitritos", "value": None, "value_text": "Não detectável", "unit": None, "ref_low": None, "ref_high": None,
         "measured_on": "2024-11-11", "uncertain": False, "source_text": "Nitritos Não detectável"},
        {"test": "Colesterol Total", "value": 222, "value_text": None, "unit": "mg/dL", "ref_low": None, "ref_high": 190,
         "measured_on": "2021-05-10", "uncertain": False, "source_text": "[hist 10-05-2021: 222]"}]
    doc = (await _file(session, pw.Wire(), reply=reply)).document
    entry = store.entry_for(session, doc.id)
    ms = {(m.test, m.measured_on.year): m for m in session.exec(
        select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)).all()}
    assert ms[("Microalbuminúria", 2024)].value is None and ms[("Microalbuminúria", 2024)].value_text == "<3"
    assert ms[("Nitritos", 2024)].value_text == "Não detectável"
    assert not ms[("Microalbuminúria", 2024)].needs_check and not ms[("Nitritos", 2024)].needs_check
    assert ms[("Colesterol Total", 2021)].value == 222                      # the printed history is data too
    from app.domains.fields import load_fields
    assert "NIF: 123456789" in load_fields(doc)["details"]


@pytest.mark.asyncio
async def test_what_a_person_typed_is_not_overwritten_by_the_reading(session):
    wire = pw.Wire()
    wire.script = [gateway_text_result(json.dumps(REPLY))] * 2 + [gateway_text_result(json.dumps(WIKI_REPLY))]
    set_gateway_provider(lambda: wire.client())
    result = await ingest(session, IncomingFile("lab.png", b"\x89PNG\r\n\x1a\nlab2", DocumentSource.MANUAL, "test"),
                          Classification(Domain.HEALTH, "lab_result", {"provider": "Dr. Costa"}))
    assert store.entry_for(session, result.document.id).provider == "Dr. Costa"


@pytest.mark.asyncio
async def test_a_failed_reading_leaves_the_paper_needing_attention_with_a_plain_reason(session):
    wire = pw.Wire()
    wire.script = [gateway_text_result("not json")]
    set_gateway_provider(lambda: wire.client())
    result = await ingest(session, IncomingFile("lab.png", b"\x89PNG\r\n\x1a\nbad", DocumentSource.MANUAL, "test"),
                          Classification(Domain.HEALTH, "lab_result", {}))
    assert result.document.status == DocumentStatus.NEEDS_ATTENTION
    assert "Health processing failed" in result.document.failure_reason


@pytest.mark.asyncio
async def test_editing_fields_updates_the_entry_columns(session):
    from app.services.ingestion import update_document_fields
    wire = pw.Wire()
    result = await _file(session, wire, content=b"\x89PNG\r\n\x1a\nlab3")
    doc = result.document
    calls_before = len(wire.parsed())
    await update_document_fields(session, doc, {"person": "Maria Santos Almeida", "provider": "Clínica Sul",
                                                "document_date": "2025-01-05", "record_kind": "check_up"})
    entry = store.entry_for(session, doc.id)
    assert (entry.provider, entry.year, entry.kind) == ("Clínica Sul", 2025, "check_up")
    assert len(wire.parsed()) == calls_before           # an edit makes no model call
