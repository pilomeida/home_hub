"""The reading is checked against the paper's own text: a value that is not on the paper, or a printed number the
reading has nowhere, is caught; one short second look asks about exactly those lines; what is still doubtful is a note."""

import json
from datetime import date

import pytest

from app.domains.health import extraction as ex
from app.domains.health import store
from app.domains.health.checks import check_reading, numbers_in, unresolved_notes
from app.domains.health.reading import read_health_document
from app.llm_gateway import GatewayError
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.test_health_extraction import _png

SOURCE = (
    "ANÁLISES CLÍNICAS\nData de colheita: 11-11-2024\nHEMATOLOGIA\n"
    "Hemoglobina  16,0  g/dL  13,0 - 17,0\nGlicose  87  mg/dL  60 - 110\nColesterol total  258  mg/dL\nPág. 1/1\n")

FULL = {
    "category": "lab_result", "person_name": "Maria Santos", "document_date": "2024-11-11", "provider": "Laboratório Norte",
    "record_kind": "test", "areas": ["Sangue"], "summary": "Análises de sangue.", "details": "Data de colheita: 11-11-2024",
    "sections": [{"path": ["Hematologia"], "results": [
        {"test": "Hemoglobina", "value": 16.0, "unit": "g/dL", "reference": "13,0 - 17,0"},
        {"test": "Glicose", "value": 87, "unit": "mg/dL", "reference": "60 - 110"},
        {"test": "Colesterol total", "value": 258, "unit": "mg/dL"}]}],
}


def _without(reply, test):
    out = json.loads(json.dumps(reply))
    out["sections"][0]["results"] = [r for r in out["sections"][0]["results"] if r["test"] != test]
    return out


def _fake(*replies, errors=None):
    return FakeGateway([gateway_text_result(json.dumps(r)) for r in replies], errors=errors)


@pytest.fixture
def paper(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "paper_text", lambda path: SOURCE)
    return _png(tmp_path)


def test_numbers_are_read_the_way_a_lab_prints_them():
    assert numbers_in("Eritrócitos  5,54  x10^6/µL  4,20 - 5,50 | 135-150 | 10-05-2021 | NP EN ISO 9001:2015 | <3 | -3,5") \
        == [5.54, 6.0, 4.2, 5.5, 135.0, 150.0, 10.0, 5.0, 2021.0, 9001.0, 2015.0, 3.0, -3.5]


@pytest.mark.asyncio
async def test_a_complete_and_faithful_reading_passes_with_one_call_and_no_notes(paper):
    fake = _fake(FULL)
    got = await read_health_document(paper, None, gateway=fake)
    assert len(fake.requests) == 1 and got.check_notes == []
    assert not any(m.uncertain for m in got.measurements)


@pytest.mark.asyncio
async def test_a_dropped_row_is_asked_about_once_with_only_that_line_and_merged(paper):
    fake = _fake(_without(FULL, "Glicose"),
                 {"sections": [{"path": ["Hematologia"], "results": [
                     {"test": "Glicose", "value": 87, "unit": "mg/dL", "reference": "60 - 110"}]}]})
    got = await read_health_document(paper, None, gateway=fake)
    assert len(fake.requests) == 2
    second = fake.requests[1]
    assert second["sensitivity"] == "house_high" and "Glicose  87" in second["user"] or "Glicose 87" in second["user"]
    assert "Hemoglobina" not in second["user"] and "- Hematologia" in second["user"]         # only the missing line + the headings
    glucose = next(m for m in got.measurements if m.test == "Glicose")
    assert (glucose.value, glucose.group_path, glucose.reference_text) == (87.0, "Hematologia", "60 - 110")
    assert glucose.position > max(m.position for m in got.measurements if m.test != "Glicose")
    assert got.check_notes == []


@pytest.mark.asyncio
async def test_a_value_that_is_not_on_the_paper_is_marked_doubtful_and_noted(paper):
    wrong = json.loads(json.dumps(FULL))
    wrong["sections"][0]["results"][0]["value"] = 61.0                    # 16,0 read as 61
    got = await read_health_document(paper, None, gateway=_fake(wrong, {}))
    hb = next(m for m in got.measurements if m.test == "Hemoglobina")
    assert hb.uncertain is True
    assert any("Hemoglobina" in n and "61" in n and "not printed on the paper" in n for n in got.check_notes)


@pytest.mark.asyncio
async def test_if_the_second_look_fails_the_reading_stays_and_the_note_says_so(paper):
    class Boom(FakeGateway):
        async def run(self, *a, **k):
            if self.requests:
                self.requests.append(k)
                raise GatewayError(502, "route down")
            return await super().run(*a, **k)
    boom = Boom([gateway_text_result(json.dumps(_without(FULL, "Glicose")))])
    got = await read_health_document(paper, None, gateway=boom)
    assert any("second look" in n and "did not work" in n for n in got.check_notes)
    assert {m.test for m in got.measurements} == {"Hemoglobina", "Colesterol total"}      # what was read is kept


@pytest.mark.asyncio
async def test_a_scan_has_nothing_to_check_against_so_one_call_and_no_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "paper_text", lambda path: None)
    fake = _fake(dict(_without(FULL, "Glicose"), full_text="texto lido da imagem"))
    got = await read_health_document(_png(tmp_path), None, gateway=fake)
    assert len(fake.requests) == 1 and got.check_notes == []


def test_page_furniture_dates_units_and_limits_are_accounted_without_noise():
    x = ex.ExtractedHealth(document_date=date(2024, 11, 11), details="Data de colheita: 11-11-2024")
    x.measurements = [ex.ExtractedMeasurement("Eritrócitos", 5.54, None, "x10^6/µL", 4.2, 5.5, None, False, "", reference_text="4,20 - 5,50")]
    source = "Pág. 2/3\nData de colheita: 11-11-2024\nEritrócitos  5,54  x10^6/µL  4,20 - 5,50\n"
    assert check_reading(x, source).clean
    assert check_reading(x, "").clean and check_reading(x, None).clean       # no text, no check


def test_notes_are_in_plain_words():
    x = ex.ExtractedHealth()
    report = check_reading(x, "Ferro  45  ug/dL  60 - 170\n")
    assert report.uncaptured_lines == ["Ferro 45 ug/dL 60 - 170"]
    assert unresolved_notes(report) == ['Printed on the paper but not found in what was read: "Ferro 45 ug/dL 60 - 170"']


@pytest.mark.asyncio
async def test_notes_are_kept_on_the_paper_count_as_to_check_and_clear_when_a_person_has_looked(client, session, tmp_path, monkeypatch):
    from tests import privacy_world as pw
    from tests.test_health_handler import _file, WIKI_REPLY
    monkeypatch.setattr(ex, "paper_text", lambda path: SOURCE)
    wrong = json.loads(json.dumps(FULL))
    wrong["sections"][0]["results"][0]["value"] = 61.0
    wire = pw.Wire()
    wire.script = [gateway_text_result(json.dumps(wrong)), gateway_text_result("{}"), gateway_text_result(json.dumps(WIKI_REPLY))]
    from app.llm_gateway import set_gateway_provider
    from app.models.document import DocumentSource
    from app.models.domain import Domain
    from app.services.ingestion import Classification, IncomingFile, ingest
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "documents")
    set_gateway_provider(lambda: wire.client())
    try:
        doc = (await ingest(session, IncomingFile("lab.png", b"\x89PNG\r\n\x1a\nq", DocumentSource.MANUAL, "t"),
                            Classification(Domain.HEALTH, "lab_result", {}))).document
    finally:
        set_gateway_provider(None)
    entry = store.entry_for(session, doc.id)
    assert entry.needs_check and store.notes_of(entry)
    page = client.get(f"/health/documents/{doc.id}").text
    assert "to look at" in page and "not printed on the paper" in page
    assert [b["sensitivity"] for b in wire.parsed()] == ["house_high"] * 3      # read, second look, wiki: all High
    client.post(f"/health/documents/{doc.id}/notes/ok", follow_redirects=False)
    session.expire_all()
    assert store.notes_of(store.entry_for(session, doc.id)) == []
