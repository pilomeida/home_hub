"""Verification: every paper is read twice, independently; where the two
readings disagree the person is told in plain words. A person can also confirm a paper's outline, which then guides
the later readings of that provider's papers."""

import copy
import json

import pytest
from sqlmodel import select

from app.domains.health import extraction as ex
from app.domains.health import outlines, reading, store
from app.domains.health.checks import compare_readings
from app.domains.health.text import PATH_SEP
from app.llm_gateway import HEALTH_EXTRACTION, GatewayError
from app.models.domain import Domain
from app.models.health import HealthOutlineProfile
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
                     {"test": "Índice de pulsatilidade", "value": 0.9, "unit": "u"}]},
    ],
}
SHEET = {"category": "lab_result", "person_name": "Maria Santos Almeida", "document_date": "2026-03-12",
         "provider": "Laboratório Norte", "record_kind": "test", "summary": "Blood panel.", "layout": "sheet",
         "sections": [{"path": ["Bioquímica"], "results": [{"test": "Glucose", "value": 90, "unit": "mg/dL"}]}]}


def _gw(*replies):
    return FakeGateway([gateway_text_result(json.dumps(r)) for r in replies])


async def _read(tmp_path, reply):
    return await ex.extract_health_document(_png(tmp_path), None, gateway=_gw(reply))


def _changed(**top):
    r = copy.deepcopy(REPORT)
    r.update(top)
    return r


async def _notes(tmp_path, other):
    return compare_readings(await _read(tmp_path, REPORT), await _read(tmp_path, other))


# --- compare_readings -------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_identical_readings_and_spacing_only_wording_differences_give_no_notes(tmp_path):
    assert await _notes(tmp_path, REPORT) == []
    spaced = copy.deepcopy(REPORT)
    spaced["sections"][0]["results"][0]["value_text"] = "Permeabilidade   do  eixo carotídeo. "
    assert await _notes(tmp_path, spaced) == []


@pytest.mark.asyncio
async def test_a_row_found_in_only_one_reading_is_named(tmp_path):
    fewer = copy.deepcopy(REPORT)
    fewer["sections"][1]["results"].pop()                      # the pulsatility index is missing from the second
    a, b = await _read(tmp_path, REPORT), await _read(tmp_path, fewer)
    expect = "Only one of two readings found: ECO/DTC › Índice de pulsatilidade. Look at the paper."
    assert compare_readings(a, b) == [expect]
    assert compare_readings(b, a) == [expect]                  # either way round


@pytest.mark.asyncio
async def test_a_value_that_differs_is_named_with_both_values(tmp_path):
    other = copy.deepcopy(REPORT)
    other["sections"][1]["results"][1]["value"] = 0.8
    notes = await _notes(tmp_path, other)
    assert notes == ["Two readings gave different values for ECO/DTC › Índice de pulsatilidade: 0.9 and 0.8."]


@pytest.mark.asyncio
async def test_a_number_in_one_reading_and_words_or_nothing_in_the_other_is_named(tmp_path):
    words = copy.deepcopy(REPORT)
    words["sections"][1]["results"][1] = {"test": "Índice de pulsatilidade", "value_text": "ilegível", "unit": "u"}
    expect = ("Two readings disagree about ECO/DTC › Índice de pulsatilidade: one found 0.9, "
              "the other found ilegível. Look at the paper.")
    assert await _notes(tmp_path, words) == [expect]
    assert compare_readings(await _read(tmp_path, words), await _read(tmp_path, REPORT)) == [expect]      # either way round
    empty = copy.deepcopy(REPORT)
    empty["sections"][1]["results"][1] = {"test": "Índice de pulsatilidade", "unit": "u"}
    assert await _notes(tmp_path, empty) == [
        "Two readings disagree about ECO/DTC › Índice de pulsatilidade: one found 0.9, the other found no value. Look at the paper."]


@pytest.mark.asyncio
async def test_equal_numbers_and_equal_words_give_no_note_and_the_lopsided_kind_is_capped_at_five(tmp_path):
    same = {"layout": "report", "sections": [{"path": ["Achados"], "results": [
        {"test": "Medida", "value": 5.3, "unit": "u"}, {"test": "Texto", "value_text": "Sem alterações."}]}]}
    assert compare_readings(await _read(tmp_path, same), await _read(tmp_path, copy.deepcopy(same))) == []
    nums = {"layout": "report", "sections": [{"path": ["Achados"], "results": [
        {"test": f"Medida {i}", "value": float(i)} for i in range(8)]}]}
    words = {"layout": "report", "sections": [{"path": ["Achados"], "results": [
        {"test": f"Medida {i}", "value_text": "ilegível"} for i in range(8)]}]}
    notes = compare_readings(await _read(tmp_path, nums), await _read(tmp_path, words))
    assert len(notes) == 5 and all(n.startswith("Two readings disagree about Medida") for n in notes)


@pytest.mark.asyncio
async def test_wording_that_differs_is_flagged_under_its_heading(tmp_path):
    other = copy.deepcopy(REPORT)
    other["sections"][0]["results"][0]["value_text"] = "Estenose significativa com placa calcificada."
    notes = await _notes(tmp_path, other)
    assert notes == ["The wording read under Triplex carotídeo › Eixo Carotídeo direito differs between two readings. "
                     "Look at the paper."]


@pytest.mark.asyncio
async def test_a_different_date_or_person_is_flagged(tmp_path):
    assert await _notes(tmp_path, _changed(document_date="2023-03-27")) == [
        "The two readings disagree about the date of the paper. Look at the paper."]
    assert await _notes(tmp_path, _changed(person_name="Maria Santos Almeida")) == [
        "The two readings disagree about whose paper it is. Look at the paper."]


@pytest.mark.asyncio
async def test_at_most_five_notes_of_each_kind_in_a_fixed_order(tmp_path):
    one = {"layout": "report", "sections": [{"path": ["Achados"], "results": (
        [{"test": f"Medida {i}", "value": float(i)} for i in range(8)] + [{"test": f"Outra {i}", "value": 1.0} for i in range(8)])}]}
    two = {"layout": "report", "sections": [{"path": ["Achados"], "results": [
        {"test": f"Medida {i}", "value": float(i) + 1} for i in range(8)]}]}
    notes = compare_readings(await _read(tmp_path, one), await _read(tmp_path, two))
    only = [n for n in notes if n.startswith("Only one of two")]
    diff = [n for n in notes if n.startswith("Two readings gave")]
    assert len(only) == 5 and len(diff) == 5 and notes == only + diff


# --- read_health_document ---------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_report_is_read_twice_the_first_is_stored_and_the_difference_is_noted(tmp_path):
    fewer = copy.deepcopy(REPORT)
    fewer["sections"][1]["results"].pop()
    fake = _gw(REPORT, fewer)
    got = await reading.read_health_document(_png(tmp_path), None, gateway=fake)
    assert len(got.measurements) == 3                          # the first reading is the one stored
    assert "Only one of two readings found: ECO/DTC › Índice de pulsatilidade. Look at the paper." in got.check_notes
    assert [r["workload_type"] for r in fake.requests] == [HEALTH_EXTRACTION, HEALTH_EXTRACTION]
    assert [r["sensitivity"] for r in fake.requests] == ["house_high", "house_high"]
    assert fake.requests[0]["system"] == fake.requests[1]["system"] and fake.requests[0]["user"] == fake.requests[1]["user"]


@pytest.mark.asyncio
async def test_every_paper_is_read_twice_a_sheet_with_a_text_layer_as_much_as_an_image(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "paper_text", lambda path: "Glucose   90  mg/dL")
    pdf = tmp_path / "lab.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    text_layer = _gw(SHEET, SHEET)
    got = await reading.read_health_document(str(pdf), None, gateway=text_layer)
    assert len(text_layer.requests) == 2 and got.check_notes == []

    monkeypatch.setattr(ex, "paper_text", lambda path: None)
    two = _gw(SHEET, SHEET)
    got = await reading.read_health_document(_png(tmp_path), None, gateway=two)
    assert len(two.requests) == 2 and got.check_notes == []    # the two agree: nothing to say

    off = _gw(SHEET)
    await reading.read_health_document(_png(tmp_path), None, gateway=off, second_read=False)
    assert len(off.requests) == 1


def _a1c_sheet(path):
    return {"category": "lab_result", "person_name": "Maria Santos Almeida", "document_date": "2026-03-12",
            "provider": "Laboratório Norte", "record_kind": "test", "summary": "Blood panel.", "layout": "sheet",
            "sections": [{"path": path, "results": [{"test": "HbA1c", "value": 5.4, "unit": "%"}]}]}


@pytest.mark.asyncio
async def test_a_text_layer_sheet_whose_two_readings_structure_a_row_differently_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "paper_text", lambda path: "HbA1c   5.4  %")
    pdf = tmp_path / "lab.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    fake = _gw(_a1c_sheet(["Diabetes"]), _a1c_sheet([]))       # under a heading in one reading, flat in the other
    got = await reading.read_health_document(str(pdf), None, gateway=fake)
    assert len(fake.requests) == 2
    assert any(n.startswith("Only one of two readings found:") and "HbA1c" in n for n in got.check_notes)
    same = _gw(_a1c_sheet(["Diabetes"]), _a1c_sheet(["Diabetes"]))
    assert (await reading.read_health_document(str(pdf), None, gateway=same)).check_notes == []


@pytest.mark.asyncio
async def test_a_failing_second_reading_leaves_the_first_intact_and_says_so(tmp_path):
    flaky = FakeGateway(results=[gateway_text_result(json.dumps(REPORT))])
    run = flaky.run
    calls = []

    async def run_then_fail(*a, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise GatewayError(503, "overloaded")
        return await run(*a, **kw)

    flaky.run = run_then_fail
    got = await reading.read_health_document(_png(tmp_path), None, gateway=flaky)
    assert len(got.measurements) == 3 and got.layout == "report"
    assert got.check_notes == ["A second reading to compare against did not work (GatewayError); this reading is unchecked. Read the paper again to check it."]


@pytest.mark.asyncio
async def test_more_notes_than_fit_end_with_a_count_and_what_to_do(tmp_path):
    def results(names, shift=0.0):
        return {"layout": "report", "document_date": "2023-03-26", "sections": [{"path": ["Achados"], "results": [
            {"test": n, "value": float(i) + shift} for i, n in enumerate(names)]}]}
    first = results([f"Medida {i}" for i in range(8)] + [f"Outra {i}" for i in range(8)])
    second = results([f"Medida {i}" for i in range(8)], shift=1.0)
    second["document_date"] = "2023-03-27"                 # 5 "only one found" + 5 "different value" + 1 date = 11 notes
    got = await reading.read_health_document(_png(tmp_path), None, gateway=_gw(first, second))
    assert len(got.check_notes) == reading.MAX_NOTES + 1
    assert got.check_notes[-1] == "... and 1 more. Look at the paper."


# --- confirm_outline --------------------------------------------------------------------------------------------

async def _entry(session, tmp_path, reply=REPORT):
    extracted = await _read(tmp_path, reply)
    doc = make_document(session, domain=Domain.HEALTH, category=extracted.category, filename="r.pdf")
    return store.apply_extraction(session, doc, extracted)


@pytest.mark.asyncio
async def test_confirming_an_outline_stores_a_profile_that_then_guides_readings(session, tmp_path):
    entry = await _entry(session, tmp_path)
    profile = outlines.confirm_outline(session, entry, "Pedro")
    assert profile.provider == "Hospital de Santa Maria" and profile.provider_key == "hospital de santa maria"
    assert profile.layout == "report" and profile.confirmed_by == "Pedro"
    assert json.loads(profile.outline_json) == [
        PATH_SEP.join(["Conclusões", "Triplex carotídeo", "Eixo Carotídeo direito"]),
        PATH_SEP.join(["Conclusões", "ECO/DTC"])]
    session.refresh(entry)
    assert entry.outline_confirmed_by == "Pedro" and entry.outline_confirmed_at is not None
    text = outlines.guidance(session)
    assert "Hospital de Santa Maria" in text and "- Conclusões › ECO/DTC" in text


@pytest.mark.asyncio
async def test_a_second_confirmation_replaces_the_profile(session, tmp_path):
    entry = await _entry(session, tmp_path)
    outlines.confirm_outline(session, entry, "Pedro")
    other = copy.deepcopy(REPORT)
    other["sections"] = [{"path": ["Conclusões", "DTC-c"], "results": [{"test": "Conclusão", "value_text": "Sem sinais."}]}]
    entry2 = await _entry(session, tmp_path, other)
    outlines.confirm_outline(session, entry2, None)
    rows = session.exec(select(HealthOutlineProfile)).all()
    assert len(rows) == 1 and json.loads(rows[0].outline_json) == [PATH_SEP.join(["Conclusões", "DTC-c"])]
    assert rows[0].confirmed_by is None


@pytest.mark.asyncio
async def test_confirming_without_a_provider_or_results_is_explained(session, tmp_path):
    entry = await _entry(session, tmp_path, dict(REPORT, provider=None))
    entry.provider = None
    session.add(entry)
    session.commit()
    with pytest.raises(outlines.OutlineError) as e:
        outlines.confirm_outline(session, entry, "Pedro")
    p = e.value.problem
    assert p.what and p.why and p.next_step and e.value.status == 409
    empty = await _entry(session, tmp_path, dict(REPORT, sections=[]))
    with pytest.raises(outlines.OutlineError) as e2:
        outlines.confirm_outline(session, empty, "Pedro")
    assert e2.value.problem.what and e2.value.problem.why and e2.value.problem.next_step and e2.value.status == 409
    assert session.exec(select(HealthOutlineProfile)).all() == []
