"""A health paper's context (why it was made, clinical notes, who asked, who did it) is read, kept as fields,
counted by the check against the paper, and a person's own words are never overwritten by a reading."""

import json

import pytest

from app.domains.fields import dump_fields, load_fields
from app.domains.health import extraction as ex
from app.domains.health import store
from app.domains.health.checks import _strings_of, check_reading
from app.models.domain import Domain
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.knowledge_factories import make_document
from tests.test_health_extraction import _png

CONTEXT_REPLY = {
    "category": "imaging_report", "person_name": "Pedro Nuno dos Santos Almeida", "document_date": "2026-03-26",
    "provider": "Hospital de Santa Maria", "record_kind": "test", "summary": "Doppler transcraniano sem alterações.",
    "indication": "Avaliação da Circulação Cervicoencefálica",
    "clinical_notes": "AVC isquémico aos 40 anos. Provável AVC cardioembólico. Pesquisa de MES.",
    "requested_by": "C.E Neurologia", "performed_by": "Cpl. Dra. Mª Fátima Soares",
    "details": "Exame Nº: 12935", "sections": [],
}


async def _read(tmp_path, reply=CONTEXT_REPLY):
    return await ex.extract_health_document(_png(tmp_path), None, gateway=FakeGateway([gateway_text_result(json.dumps(reply))]))


@pytest.mark.asyncio
async def test_the_four_context_items_are_read(tmp_path):
    got = await _read(tmp_path)
    assert got.indication == "Avaliação da Circulação Cervicoencefálica"
    assert got.clinical_notes.startswith("AVC isquémico aos 40 anos")
    assert (got.requested_by, got.performed_by) == ("C.E Neurologia", "Cpl. Dra. Mª Fátima Soares")


@pytest.mark.asyncio
async def test_a_paper_without_context_gives_empty_strings(tmp_path):
    reply = {k: v for k, v in CONTEXT_REPLY.items() if k not in ("indication", "clinical_notes", "requested_by", "performed_by")}
    got = await _read(tmp_path, reply)
    assert (got.indication, got.clinical_notes, got.requested_by, got.performed_by) == ("", "", "", "")


@pytest.mark.asyncio
async def test_the_context_is_stored_as_fields_and_a_persons_own_words_win(session, tmp_path):
    document = make_document(session, domain=Domain.HEALTH, category="imaging_report")
    document.fields_json = dump_fields({"indication": "Controlo anual pedido pela neurologia"})   # typed by a person
    session.add(document)
    session.commit()
    store.apply_extraction(session, document, await _read(tmp_path))
    fields = load_fields(document)
    assert fields["indication"] == "Controlo anual pedido pela neurologia"          # the reading only fills blanks
    assert fields["clinical_notes"].startswith("AVC isquémico") and fields["requested_by"] == "C.E Neurologia"
    assert fields["performed_by"] == "Cpl. Dra. Mª Fátima Soares"


@pytest.mark.asyncio
async def test_the_check_against_the_paper_counts_the_context_as_read_text(tmp_path):
    got = await _read(tmp_path)
    strings = [s for s in _strings_of(got) if s]
    assert got.clinical_notes in strings and got.indication in strings        # their numbers ("40 anos") are not "missing"
    source = "Informação clínica: AVC isquémico aos 40 anos\n"
    assert "AVC isquémico aos 40 anos" not in check_reading(got, source).uncaptured_lines and check_reading(got, source).clean
