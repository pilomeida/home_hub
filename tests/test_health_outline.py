"""A paper printed as an outline (main sections with chapters inside) is stored as that outline: conclusions are
results under the section or chapter they describe, and every number is a result in its own section."""

import json

import pytest

from app.domains.health import extraction as ex
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.test_health_extraction import _png

OUTLINE_REPLY = {
    "category": "imaging_report", "person_name": "Pedro Nuno dos Santos Almeida", "document_date": "2026-03-26",
    "provider": "Hospital de Santa Maria", "record_kind": "test", "summary": "Doppler transcraniano.",
    "sections": [
        {"path": ["Triplex carotídeo", "Eixo Carotídeo direito"], "results": [
            {"test": "Velocidade sistólica máxima", "value": 85, "unit": "cm/s"},
            {"test": "Conclusão", "value_text": "Permeabilidade do eixo carotídeo, com pequena placa ateromatosa sem repercussão hemodinâmica."}]},
        {"path": ["Triplex carotídeo", "Eixo Carotídeo esquerdo"], "results": [
            {"test": "Conclusão", "value_text": "Permeabilidade do eixo carotídeo com velocidades normais."}]},
        {"path": ["Triplex carotídeo", "Artérias Vertebrais"], "results": [
            {"test": "Conclusão", "value_text": "Permeáveis; dominância da artéria vertebral direita."}]},
        {"path": ["ECO/DTC"], "results": [
            {"test": "Índice de pulsatilidade", "value": 0.9},
            {"test": "Conclusão", "value_text": "Permeabilidade de todos os segmentos estudados."}]},
        {"path": ["DTC-c"], "results": [
            {"test": "Conclusão", "value_text": "Sem sinais microembólicos nem shunt direito-esquerdo."}]},
    ],
}


@pytest.mark.asyncio
async def test_chapters_sit_under_their_main_section_and_each_conclusion_under_what_it_describes(tmp_path):
    got = await ex.extract_health_document(_png(tmp_path), None, gateway=FakeGateway([gateway_text_result(json.dumps(OUTLINE_REPLY))]))
    rows = [(m.group_path, m.test) for m in got.measurements]
    assert rows == [
        ("Triplex carotídeo › Eixo Carotídeo direito", "Velocidade sistólica máxima"),
        ("Triplex carotídeo › Eixo Carotídeo direito", "Conclusão"),
        ("Triplex carotídeo › Eixo Carotídeo esquerdo", "Conclusão"),
        ("Triplex carotídeo › Artérias Vertebrais", "Conclusão"),
        ("ECO/DTC", "Índice de pulsatilidade"),
        ("ECO/DTC", "Conclusão"),
        ("DTC-c", "Conclusão"),
    ]
    first = got.measurements[0]
    assert (first.value, first.unit) == (85.0, "cm/s")                              # numbers are tracked like lab values
    conclusion = got.measurements[1]
    assert conclusion.value is None and conclusion.value_text.startswith("Permeabilidade do eixo")


def test_the_prompt_teaches_the_outline_prose_conclusions_and_numbers():
    rules = ex._SYSTEM
    assert "main sections" in rules and "chapters" in rules
    assert 'test "Conclusão"' in rules and "value_text" in rules
    assert "Never put a heading's name inside a test's name" in rules
    assert "Every number printed in a section" in rules
    assert "any printed note or comment that is not a section's conclusion, impression or comment (those are results, see sections)" in rules
