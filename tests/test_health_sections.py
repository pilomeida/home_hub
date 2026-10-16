"""The reading keeps the paper's own structure: results under their headings, in the paper's order, with the
reference as printed, earlier results beside their test, and the text taken from the PDF itself when it has any."""

import json

import pytest
from sqlmodel import select

from app.domains.health import extraction as ex
from app.domains.health import store
from app.domains.health.text import limits_from_reference, reference_for_display, spaced_intervals, split_method
from app.models.health import HealthMeasurement
from tests import privacy_world as pw
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.test_health_extraction import _png
from tests.test_health_handler import _file

# The shape the model really returns for the 3-page lab report (2026-10-16 run), trimmed.
SECTIONS_REPLY = {
    "category": "lab_result", "person_name": "Maria Santos Almeida", "document_date": "2024-11-11",
    "provider": "Laboratório Norte", "record_kind": "test", "areas": ["Sangue", "Urina"], "conditions": [],
    "summary": "Análises de sangue e urina.", "details": "NIF: 123456789",
    "sections": [
        {"path": ["Hematologia", "Hemograma (Mét.CF/COL)", "Leucograma"], "results": [
            {"test": "Neutrófilos", "value": 76.5, "unit": "%"},
            {"test": "Neutrófilos", "value": 5.42, "unit": "x10^9/L", "reference": "1,50-8,00"}]},
        {"path": ["Bioquímica", "Colesterol"], "results": [
            {"test": "Colesterol HDL (Mét.EC Homogéneo)", "value": 60, "unit": "mg/dL",
             "reference": "Risco elevado:<40; Risco baixo :>60", "previous": [{"date": "2021-05-10", "value": 54}]}]},
        {"path": ["Bioquímica", "Proteína C reativa", "Doseamento"], "results": [
            {"test": "Doseamento", "value": 0.14, "unit": "mg/dL", "reference": "< 0,50"}]},
        {"path": ["Estudo da urina", "Urina tipo II", "Elementos anormais"], "results": [
            {"test": "Nitritos", "value_text": "Não detetável", "previous": [{"date": "2021-05-10", "value_text": "Não detectável"}]}]},
    ],
}


@pytest.mark.asyncio
async def test_results_come_back_under_their_headings_in_order_with_references_methods_and_history(tmp_path):
    got = await ex.extract_health_document(_png(tmp_path), None, gateway=FakeGateway([gateway_text_result(json.dumps(SECTIONS_REPLY))]))
    rows = [(m.position, m.test, m.group_path, m.is_previous) for m in got.measurements]
    assert rows == [
        (1, "Neutrófilos", "Hematologia › Hemograma › Leucograma", False),
        (2, "Neutrófilos", "Hematologia › Hemograma › Leucograma", False),
        (3, "Colesterol HDL", "Bioquímica › Colesterol", False),
        (3, "Colesterol HDL", "Bioquímica › Colesterol", True),                  # earlier result shares its test's place
        (4, "Doseamento", "Bioquímica › Proteína C reativa", False),            # the heading equal to the test is not repeated
        (5, "Nitritos", "Estudo da urina › Urina tipo II › Elementos anormais", False),
        (5, "Nitritos", "Estudo da urina › Urina tipo II › Elementos anormais", True),
    ]
    by = {(m.test, m.is_previous, m.value, m.value_text): m for m in got.measurements}
    absolute = by[("Neutrófilos", False, 5.42, None)]
    assert absolute.reference_text == "1,50 - 8,00" and (absolute.ref_low, absolute.ref_high) == (1.5, 8.0)
    assert by[("Neutrófilos", False, 76.5, None)].reference_text is None         # a reference belongs to its own unit
    hdl = by[("Colesterol HDL", False, 60.0, None)]
    assert hdl.method == "EC Homogéneo" and hdl.reference_text == "Risco elevado:<40; Risco baixo :>60"
    assert (hdl.ref_low, hdl.ref_high) == (60.0, None)                          # the low-risk limit decides "out of range"
    assert by[("Neutrófilos", False, 5.42, None)].method == "CF/COL"             # the heading's method reaches its results
    assert by[("Nitritos", False, None, "Não detetável")].value_text == "Não detetável"
    assert by[("Colesterol HDL", True, 54.0, None)].measured_on.isoformat() == "2021-05-10"


@pytest.mark.asyncio
async def test_a_pdf_with_its_own_text_goes_as_that_text_plus_its_pages_and_always_at_high(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "paper_text", lambda path: "ANÁLISES CLÍNICAS\nGlucose   87  mg/dL   60 - 110")
    pdf = tmp_path / "lab.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    fake = FakeGateway([gateway_text_result(json.dumps(SECTIONS_REPLY))])
    got = await ex.extract_health_document(str(pdf), None, gateway=fake)
    req = fake.requests[0]
    assert req["attachments"] and "Glucose   87  mg/dL" in req["user"]      # the text for exactness, the pages for structure
    assert "do not return full_text" in req["system"] and "Take every number and word from the TEXT" in req["system"]
    assert req["sensitivity"] == "house_high" and req["max_tokens"] is None
    assert got.full_text == "ANÁLISES CLÍNICAS\nGlucose   87  mg/dL   60 - 110"        # the paper's own text, not the model's


def test_the_prompt_returns_both_results_of_a_line_that_prints_two():
    assert "return BOTH" in ex._SYSTEM and "76,5 %" in ex._SYSTEM


@pytest.mark.asyncio
async def test_a_scan_or_photo_still_goes_as_images_and_the_model_transcribes_it(tmp_path):
    reply = dict(SECTIONS_REPLY, full_text="Texto lido da imagem")
    fake = FakeGateway([gateway_text_result(json.dumps(reply))])
    got = await ex.extract_health_document(_png(tmp_path), None, gateway=fake)
    req = fake.requests[0]
    assert req["attachments"] and "full_text: the paper" in req["system"] and req["sensitivity"] == "house_high"
    assert got.full_text == "Texto lido da imagem"


def _text_pdf(path, lines):
    """A tiny valid PDF whose page carries `lines` as real text (what a lab's PDF has and a scan lacks)."""
    stream = "BT /F1 9 Tf 40 780 Td 11 TL " + " ".join(f"({l}) Tj T*" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    path.write_bytes(out)


def test_paper_text_reads_a_pdfs_own_text_and_declines_a_scan(tmp_path):
    pdf = tmp_path / "lab.pdf"
    _text_pdf(pdf, ["Glucose 87 mg/dL (60 - 110)"] * 14)
    assert "Glucose 87 mg/dL" in (ex.paper_text(str(pdf)) or "")
    thin = tmp_path / "scan.pdf"
    _text_pdf(thin, ["stamp"])
    assert ex.paper_text(str(thin)) is None                      # too little text to be the paper: use the images
    assert ex.paper_text(_png(tmp_path)) is None


@pytest.mark.asyncio
async def test_the_stored_structure_keeps_order_headings_keys_and_earlier_results(session):
    doc = (await _file(session, pw.Wire(), reply=SECTIONS_REPLY)).document
    entry = store.entry_for(session, doc.id)
    ms = session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)
                      .order_by(HealthMeasurement.position, HealthMeasurement.is_previous)).all()
    assert [m.position for m in ms] == [1, 2, 3, 3, 4, 5, 5]
    pct, absolute = ms[0], ms[1]
    assert pct.test_key != absolute.test_key                      # % and x10^9/L of the same test are not one series
    hdl_now, hdl_before = ms[2], ms[3]
    assert hdl_now.test_key == hdl_before.test_key and hdl_before.is_previous and not hdl_before.needs_check
    assert hdl_before.measured_on.isoformat() == "2021-05-10" and hdl_before.out_of_range is None
    assert hdl_now.group_path == "Bioquímica › Colesterol" and hdl_now.out_of_range is False
    assert "colesterol hdl" in hdl_now.test_key and "bioquimica" in hdl_now.test_key
    nitrite = ms[5]
    assert nitrite.value_text == "Não detetável" and nitrite.needs_check is False


@pytest.mark.asyncio
async def test_the_paper_page_shows_the_hierarchy_the_reference_as_printed_and_the_earlier_results(client, session):
    doc = (await _file(session, pw.Wire(), reply=SECTIONS_REPLY)).document
    page = client.get(f"/health/documents/{doc.id}").text
    for heading in ("Hematologia", "Hemograma", "Leucograma", "Bioquímica", "Colesterol", "Estudo da urina",
                    "Urina tipo II", "Elementos anormais", "Proteína C reativa"):
        assert f">{heading}</th>" in page
    assert page.index(">Hematologia</th>") < page.index(">Bioquímica</th>") < page.index(">Estudo da urina</th>")
    assert page.count(">Bioquímica</th>") == 1                  # a heading is shown once while its results continue
    from markupsafe import escape
    assert "1,50 - 8,00" in page and str(escape("Risco elevado:<40; Risco baixo :>60")) in page
    assert "EC Homogéneo" in page                                # the method is kept, apart from the name
    assert "Não detetável" in page and "Não detectável" in page  # today's result and the earlier one


def test_intervals_get_spaces_and_a_reference_falls_back_to_the_numbers():
    assert spaced_intervals("4,20-5,50") == "4,20 - 5,50" and spaced_intervals("4,20 – 5,50") == "4,20 - 5,50"
    assert spaced_intervals("< 20") == "< 20"
    assert reference_for_display(None, 1.5, 8.0) == "1.5 - 8" and reference_for_display(None, None, 50.0) == "< 50"
    assert reference_for_display("13,0-17,0", None, None) == "13,0 - 17,0"
    assert reference_for_display(None, None, None) == ""


def test_limits_come_from_what_was_printed_and_a_method_is_kept_apart():
    assert limits_from_reference("4,20 - 5,50") == (4.2, 5.5) and limits_from_reference("=< 15,0") == (None, 15.0)
    assert limits_from_reference("Risco elevado: > 240; Risco baixo: < 190") == (None, 190.0)
    assert limits_from_reference("Risco elevado: < 40; Risco baixo: > 60") == (60.0, None)
    assert limits_from_reference("Risco elevado: > 5,0") == (None, 5.0)
    assert limits_from_reference("Não detectável") == (None, None) and limits_from_reference(None) == (None, None)
    assert split_method("CREATININA (Mét.Jaffé cinético compensado)") == ("CREATININA", "Jaffé cinético compensado")
    assert split_method("Nitritos") == ("Nitritos", None)


@pytest.mark.asyncio
async def test_details_given_as_a_list_of_lines_are_kept_not_rejected(tmp_path):
    from jsonschema import validate
    reply = dict(SECTIONS_REPLY, details=["NIF: 123456789", "Requisitada por: Dra. Ana Silva"])
    validate(reply, ex._SCHEMA)                                   # the gateway checks the reply against this
    got = await ex.extract_health_document(_png(tmp_path), None, gateway=FakeGateway([gateway_text_result(json.dumps(reply))]))
    assert got.details == "NIF: 123456789\nRequisitada por: Dra. Ana Silva"


def test_the_real_shape_of_a_reply_satisfies_the_schema_the_gateway_enforces():
    from jsonschema import validate
    validate(SECTIONS_REPLY, ex._SCHEMA)


@pytest.mark.asyncio
async def test_a_test_name_that_repeats_its_heading_is_stored_without_it_so_every_reading_looks_alike(tmp_path):
    reply = dict(SECTIONS_REPLY, sections=[{"path": ["Bioquímica", "Hemoglobina glicada A1c"], "results": [
        {"test": "Hemoglobina glicada A1c - Resultado (IFCC)", "value": 35, "unit": "mmol/mol"},
        {"test": "Resultado (NGSP/DCCT)", "value": 5.3, "unit": "%"},
        {"test": "Hemoglobina glicada A1c total", "value": 1, "unit": "x"}]}])      # no separator: a real name, kept
    got = await ex.extract_health_document(_png(tmp_path), None, gateway=FakeGateway([gateway_text_result(json.dumps(reply))]))
    assert [m.test for m in got.measurements] == ["Resultado (IFCC)", "Resultado (NGSP/DCCT)", "Hemoglobina glicada A1c total"]
