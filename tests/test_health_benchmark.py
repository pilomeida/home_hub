"""The benchmark compares a fresh reading with a confirmed one, and a paper in the Hub becomes a case."""

import json
from copy import deepcopy

import pytest

from app.domains.health import benchmark
from app.domains.health import extraction as ex
from app.domains.health.reading import read_health_document
from tests import privacy_world as pw
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.test_health_checks import FULL
from tests.test_health_extraction import _png
from tests.test_health_handler import _file
from tests.test_health_sections import SECTIONS_REPLY


async def _read(tmp_path, reply):
    return await read_health_document(_png(tmp_path), None, gateway=FakeGateway([gateway_text_result(json.dumps(reply))]))


@pytest.mark.asyncio
async def test_a_reading_that_matches_its_confirmed_reading_passes(tmp_path):
    reading = await _read(tmp_path, SECTIONS_REPLY)
    result = benchmark.compare("case", benchmark.expected_from_reading(reading), await _read(tmp_path, SECTIONS_REPLY))
    assert result.passed and result.found == result.expected == 5 and result.problems == []


@pytest.mark.asyncio
async def test_every_kind_of_difference_is_named_in_plain_words(tmp_path):
    expected = benchmark.expected_from_reading(await _read(tmp_path, SECTIONS_REPLY))
    changed = deepcopy(SECTIONS_REPLY)
    changed["category"] = "medical_report"
    changed["sections"][1]["results"][0]["value"] = 61                                   # HDL value wrong
    changed["sections"][1]["results"][0]["reference"] = "Risco elevado:<40"             # reference wrong
    changed["sections"][1]["results"][0]["previous"] = []                                # earlier result lost
    changed["sections"][2]["results"] = []                                               # Doseamento missing
    changed["sections"][0]["path"] = ["Hematologia", "Outro grupo"]                      # Neutrófilos under another heading
    changed["sections"].append({"path": ["Novo"], "results": [{"test": "Ferro", "value": 50, "unit": "ug/dL"}]})
    result = benchmark.compare("case", expected, await _read(tmp_path, changed))
    text = "\n".join(result.problems)
    assert not result.passed
    assert "kind of paper: expected lab_result, read medical_report" in text
    assert "wrong value: Bioquímica › Colesterol › Colesterol HDL (mg/dL): expected 60.0, read 61.0" in text
    assert "wrong reference: " in text and "wrong earlier results: " in text
    assert "missing: Bioquímica › Proteína C reativa › Doseamento" in text
    assert "wrong heading: Hematologia › Hemograma › Leucograma › Neutrófilos" in text and "was read under 'Hematologia › Hemograma' › 'Outro grupo'" not in text
    assert any("Ferro" in n for n in result.notes)                                      # an extra result is information, not failure


@pytest.mark.asyncio
async def test_a_paper_in_the_hub_becomes_a_case_that_passes_against_itself(session, tmp_path, monkeypatch):
    doc = (await _file(session, pw.Wire(), reply=SECTIONS_REPLY)).document
    folder = benchmark.promote(session, doc.id, "lab-one", directory=tmp_path / "bench")
    assert (folder / "expected.json").is_file() and next(folder.glob("paper.*")).exists()
    expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
    assert expected["reviewed"] is False and expected["header"]["category"] == "lab_result"
    assert [r["test"] for r in expected["results"]] == ["Neutrófilos", "Neutrófilos", "Colesterol HDL", "Doseamento", "Nitritos"]
    assert expected["results"][2]["previous"] == [{"date": "2021-05-10", "value": 54.0, "value_text": None}]
    cases = benchmark.load_cases(tmp_path / "bench")
    assert [c[0] for c in cases] == ["lab-one"]
    result = benchmark.compare("lab-one", cases[0][2], await _read(tmp_path, SECTIONS_REPLY))
    assert result.passed, result.problems


def test_no_cases_when_the_folder_is_missing_or_incomplete(tmp_path):
    assert benchmark.load_cases(tmp_path / "nope") == []
    (tmp_path / "half").mkdir()
    (tmp_path / "half" / "expected.json").write_text("{}")
    assert benchmark.load_cases(tmp_path) == []


def test_the_papers_can_never_be_committed():
    import subprocess
    out = subprocess.run(["git", "check-ignore", "data/health_benchmark/x/paper.pdf"], capture_output=True, text=True, cwd=".")
    assert out.returncode == 0
