"""A written report keeps its outline: the reader names the layout, the store attaches each row to a topic (the
baseline of headings), and the topics already approved are offered back to the reader as guidance."""

import json

import pytest
from sqlmodel import select

from app.domains.health import extraction as ex
from app.domains.health import outlines, reading, store
from app.domains.health.text import PATH_SEP
from app.llm_gateway import set_gateway_provider
from app.models.document import DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.health import HealthMeasurement, HealthOutlineProfile, HealthTopic
from app.services.ingestion import Classification, IncomingFile, ingest
from tests import privacy_world as pw
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.knowledge_factories import make_document
from tests.test_health_extraction import _png

REPORT = {
    "category": "imaging_report", "person_name": "Pedro Nuno dos Santos Almeida", "document_date": "2023-03-26",
    "provider": "Hospital de Santa Maria", "record_kind": "test", "summary": "Doppler.", "layout": "report",
    "sections": [
        {"path": ["Conclusões", "Triplex carotídeo", "Eixo Carotídeo direito"],
         "results": [{"test": "Conclusão", "value_text": "Permeabilidade do eixo carotídeo."}]},
        {"path": ["Conclusões", "ECO/DTC"],
         "results": [{"test": "Conclusão", "value_text": "Permeabilidade de todos os segmentos."},
                     {"test": "Índice de pulsatilidade", "value": 0.9}]},
    ],
}
SHEET = {"category": "lab_result", "person_name": "Maria Santos Almeida", "document_date": "2026-03-12",
         "provider": "Laboratório Norte", "record_kind": "test", "summary": "Blood panel.", "layout": "sheet",
         "sections": [{"path": ["Bioquímica"], "results": [{"test": "Glucose", "value": 90, "unit": "mg/dL"}]}]}


async def _read(tmp_path, reply, **kw):
    return await ex.extract_health_document(_png(tmp_path), None, gateway=FakeGateway([gateway_text_result(json.dumps(reply))]), **kw)


async def _file_report(session, tmp_path, reply=REPORT, name="r.pdf", category="imaging_report"):
    extracted = await _read(tmp_path, reply)
    doc = make_document(session, domain=Domain.HEALTH, category=category, filename=name)
    entry = store.apply_extraction(session, doc, extracted)
    return doc, entry


def _rows(session, entry):
    return session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)
                        .order_by(HealthMeasurement.position)).all()


def _topics(session):
    return session.exec(select(HealthTopic).order_by(HealthTopic.id)).all()


@pytest.mark.asyncio
async def test_the_reader_returns_the_layout_and_ignores_an_unknown_one(tmp_path):
    assert (await _read(tmp_path, REPORT)).layout == "report"
    assert (await _read(tmp_path, SHEET)).layout == "sheet"
    assert (await _read(tmp_path, dict(REPORT, layout="poster"))).layout == ""
    assert (await _read(tmp_path, {k: v for k, v in REPORT.items() if k != "layout"})).layout == ""


@pytest.mark.asyncio
async def test_filing_a_report_attaches_each_row_to_its_deepest_topic(session, tmp_path):
    _, entry = await _file_report(session, tmp_path)
    assert entry.layout == "report"
    rows = _rows(session, entry)
    assert len(rows) == 3
    assert all(m.group_path.startswith("Conclusões" + PATH_SEP) for m in rows)         # stored as printed
    topics = _topics(session)
    assert [(t.name, t.approved) for t in topics] == [
        ("Triplex carotídeo", False), ("Eixo Carotídeo direito", False), ("ECO/DTC", False)]
    by_name = {t.name: t.id for t in topics}
    assert rows[0].topic_id == by_name["Eixo Carotídeo direito"]
    assert rows[1].topic_id == rows[2].topic_id == by_name["ECO/DTC"]


@pytest.mark.asyncio
async def test_a_sheet_gets_no_topics(session, tmp_path):
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result")
    entry = store.apply_extraction(session, doc, await _read(tmp_path, SHEET))
    assert entry.layout == "sheet"
    assert [m.topic_id for m in _rows(session, entry)] == [None]
    assert _topics(session) == []


@pytest.mark.asyncio
async def test_a_paper_without_a_layout_stores_none(session, tmp_path):
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result")
    entry = store.apply_extraction(session, doc, await _read(tmp_path, {k: v for k, v in SHEET.items() if k != "layout"}))
    assert entry.layout is None


@pytest.mark.asyncio
async def test_a_later_report_reuses_the_topics_and_a_re_read_replaces_the_links(session, tmp_path):
    _, first = await _file_report(session, tmp_path, name="a.pdf")
    ids = [t.id for t in _topics(session)]
    second_reply = dict(REPORT, document_date="2025-06-01")
    doc2, second = await _file_report(session, tmp_path, second_reply, name="b.pdf")
    assert [t.id for t in _topics(session)] == ids                                      # no new topic rows
    assert {m.topic_id for m in _rows(session, second)} <= set(ids) and None not in {m.topic_id for m in _rows(session, second)}
    # re-reading the same paper re-creates the rows and links them again
    again = store.apply_extraction(session, doc2, await _read(tmp_path, second_reply))
    rows = _rows(session, again)
    assert len(rows) == 3 and None not in {m.topic_id for m in rows}
    assert [t.id for t in _topics(session)] == ids


@pytest.mark.asyncio
async def test_guidance_is_empty_then_names_approved_topics_and_confirmed_outlines(session, tmp_path):
    assert outlines.guidance(session) == ""
    await _file_report(session, tmp_path)
    assert outlines.guidance(session) == ""                                             # unapproved topics are not baseline yet
    from app.domains.health import topics as tp
    for t in _topics(session):
        tp.approve_topic(session, t.id)
    text = outlines.guidance(session)
    assert text.startswith("Topics already in the family's baseline")
    assert f"- Triplex carotídeo{PATH_SEP}Eixo Carotídeo direito" in text
    session.add(HealthOutlineProfile(provider_key="hospital santa maria", provider="Hospital de Santa Maria", layout="report",
                                     outline_json=json.dumps([f"Triplex carotídeo{PATH_SEP}Eixo Carotídeo direito", "ECO/DTC"])))
    session.commit()
    text = outlines.guidance(session)
    assert "Outline a person confirmed as right for Hospital de Santa Maria:" in text and "- ECO/DTC" in text
    short = outlines.guidance(session, max_chars=200)
    assert short.startswith("Topics already") and len(short) <= 200 and "Outline a person" not in short


@pytest.mark.asyncio
async def test_the_guidance_reaches_the_gateway(tmp_path):
    fake = FakeGateway([gateway_text_result(json.dumps(REPORT))])
    await ex.extract_health_document(_png(tmp_path), None, gateway=fake, guidance="X-MARK")
    assert "X-MARK" in fake.requests[0]["system"]
    fake2 = FakeGateway([gateway_text_result(json.dumps(REPORT))])
    await ex.extract_health_document(_png(tmp_path), None, gateway=fake2)
    assert "X-MARK" not in fake2.requests[0]["system"]
    fake3 = FakeGateway([gateway_text_result(json.dumps(REPORT))])
    await reading.read_health_document(_png(tmp_path), None, gateway=fake3, guidance="Y-MARK", second_read=False)
    assert "Y-MARK" in fake3.requests[0]["system"]


@pytest.fixture
def _gateway_reset(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "documents")
    yield
    set_gateway_provider(None)


@pytest.mark.asyncio
async def test_the_health_handler_sends_the_baseline_topics_with_the_first_request(session, tmp_path, _gateway_reset):
    from app.domains.health import topics as tp
    await _file_report(session, tmp_path)
    for t in _topics(session):
        tp.approve_topic(session, t.id)
    pw.set_levels(session, on=False)
    wire = pw.Wire()
    wire.script = [gateway_text_result(json.dumps(REPORT))] * 2 + [gateway_text_result(json.dumps({"pages": []}))]     # a report is read twice
    set_gateway_provider(lambda: wire.client())
    result = await ingest(session, IncomingFile("r.png", b"\x89PNG\r\n\x1a\nreport", DocumentSource.MANUAL, "test"),
                          Classification(Domain.HEALTH, "imaging_report", {}))
    assert result.document.status == DocumentStatus.PROCESSED and not result.document.failure_reason     # no swallowed later failure
    assert all(f"Triplex carotídeo{PATH_SEP}Eixo Carotídeo direito" in b["system"] for b in wire.parsed()[:2])     # both readings


def test_the_prompt_names_the_layout_and_the_report_rule_and_the_slice_still_works():
    assert '- layout: "sheet"' in ex._SYSTEM and ex._SYSTEM.index("- layout:") < ex._SYSTEM.index("- summary:")
    assert "In a written report the headings ARE the topics" in ex._SYSTEM
    assert ex._SYSTEM.count("{full_text_rule}") == 1 and "- sections:" in ex._SYSTEM
    assert "Conclusão" in reading._RESULTS_RULES and "{full_text_rule}" not in reading._RESULTS_RULES
    assert reading._RESULTS_RULES.startswith("- sections:")
