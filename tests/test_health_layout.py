"""PLAN step 5d: the paper's structure read with code; the model only interprets."""

from app.categories.health import layout
from app.categories.health.extraction import ExtractedHealth, ExtractedMeasurement
from app.categories.health.text import PATH_SEP
from tests.fakes.text_pdf import placed_pdf_bytes

# A lab sheet laid out like Cintramédica's: bold sections, headings alone at the left margin, results with their
# value, unit and reference in columns to the right (the reference in smaller type, on a slightly lower baseline).
SHEET = [
    (264, 700, 8.8, True, "BIOQUIMICA"),
    (27, 680, 7.0, False, "HEMOGLOBINA GLICADA A1c"), (110, 680, 5.5, False, "( Mét.Electrof. capilar )"),
    (27, 668, 7.0, False, "Resultado ( IFCC )"), (231, 668, 7.8, False, "35"), (312, 668, 6.3, False, "mmol/mol"),
    (405, 666, 5.5, False, "20 - 42"),
    (27, 656, 7.0, False, "GME ( Glicémia Média Estimada )"), (231, 656, 7.8, False, "106"),
    (27, 641.8, 7.0, False, "UREIA"), (231, 641.8, 7.8, False, "39"),           # 14.2 lower: after the block
    (264, 630, 8.8, True, "HEMATOLOGIA"),
    (27, 618, 7.0, False, "ERITROGRAMA"),
    (27, 606, 7.0, False, "RDW"), (231, 606, 7.8, False, "13,9"), (312, 606, 6.3, False, "%"),
    (415, 604, 5.5, False, "=< 15,0"),
    (27, 592, 7.0, False, "RAZÃO COL.TOTAL/COL.HDL"), (231, 592, 7.8, False, "4,3"),
    (381, 592, 5.5, False, "Risco elevado:>5,0"),
]


def _m(test, path, value, reference=None, unit=None):
    return ExtractedMeasurement(test, value, None, unit, None, None, None, False, "", group_path=path,
                                reference_text=reference)


def _pdf(tmp_path, items=SHEET):
    p = tmp_path / "lab.pdf"
    p.write_bytes(placed_pdf_bytes(items))
    return str(p)


def test_lines_sections_and_headings_are_read_from_the_pdf(tmp_path):
    lines = layout.read_lines(_pdf(tmp_path))
    kinds = [("S" if l.is_section else "H" if l.is_heading else "-", l.text) for l in lines]
    assert kinds[0] == ("S", "BIOQUIMICA")
    assert kinds[1][0] == "H" and kinds[1][1].startswith("HEMOGLOBINA GLICADA A1c")
    assert ("-", "RDW 13,9 % =< 15,0") in kinds                       # the smaller reference joins its line


def test_results_left_under_the_section_go_under_their_printed_heading(tmp_path):
    x = ExtractedHealth(measurements=[
        _m("Resultado (IFCC)", "Bioquimica", 35, "20 - 42", "mmol/mol"),
        _m("GME (Glicémia Média Estimada)", "Bioquimica", 106),
        _m("Ureia", "Bioquimica", 39),
        _m("RDW", f"Hematologia{PATH_SEP}Hemograma{PATH_SEP}Eritrograma", 13.9, "=< 15,0", "%"),
    ])
    changed = layout.apply_layout(x, _pdf(tmp_path))
    paths = [m.group_path for m in x.measurements]
    assert paths[0] == paths[1] == f"Bioquimica{PATH_SEP}Hemoglobina glicada A1c"
    assert paths[2] == "Bioquimica"                                    # printed after the block: under the section
    assert paths[3] == f"Hematologia{PATH_SEP}Hemograma{PATH_SEP}Eritrograma"     # already deeper: left alone
    assert len(changed) == 2


def test_a_reference_the_reading_changed_is_set_back_to_what_is_printed(tmp_path):
    x = ExtractedHealth(measurements=[_m("RDW", f"Hematologia{PATH_SEP}Eritrograma", 13.9, ">= 15,0", "%")])
    changed = layout.apply_layout(x, _pdf(tmp_path))
    m = x.measurements[0]
    assert m.reference_text == "=< 15,0" and (m.ref_low, m.ref_high) == (None, 15.0)
    assert changed == ["RDW: reference >= 15,0 corrected to =< 15,0, as printed"]


def test_a_reference_already_as_printed_and_a_scan_are_left_alone(tmp_path):
    x = ExtractedHealth(measurements=[_m("RDW", f"Hematologia{PATH_SEP}Eritrograma", 13.9, "=< 15,0", "%")])
    assert layout.apply_layout(x, _pdf(tmp_path)) == []
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG\r\n")
    assert layout.apply_layout(x, str(img)) == [] and layout.read_lines(str(img)) == []


def test_a_worded_reference_or_one_differing_only_in_spaces_is_kept(tmp_path):
    x = ExtractedHealth(measurements=[
        _m("RDW", f"Hematologia{PATH_SEP}Eritrograma", 13.9, "=<15,0", "%"),
        _m("Razão col.total/col.HDL", "Hematologia", 4.3, "Risco elevado: >5,0")])
    layout.apply_layout(x, _pdf(tmp_path))
    assert [m.reference_text for m in x.measurements] == ["=<15,0", "Risco elevado: >5,0"]


def test_a_report_drops_its_wrapper_heading_and_a_cut_short_finding_is_completed():
    from app.categories.health.reading import complete_from, drop_wrappers
    full = ("Permeabilidade de todos os segmentos estudados com velocidades e sentidos de fluxo dentro dos valores "
            "normais. Índices de pulsatilidade, dentro dos valores normais.")
    x = ExtractedHealth(layout="report", measurements=[
        ExtractedMeasurement("Conclusão", None, full.split(" Índices")[0], None, None, None, None, False, "",
                             group_path=f"Conclusões{PATH_SEP}ECO/DTC"),
        ExtractedMeasurement("Nota", None, "Só o invólucro.", None, None, None, None, False, "",
                             group_path="Conclusões")])
    y = ExtractedHealth(layout="report", measurements=[
        ExtractedMeasurement("Conclusão", None, full, None, None, None, None, False, "", group_path="ECO/DTC")])
    drop_wrappers(x)
    assert [m.group_path for m in x.measurements] == ["ECO/DTC", "Conclusões"]     # alone, the wrapper stays
    complete_from(x, y)
    assert x.measurements[0].value_text == full
    sheet = ExtractedHealth(layout="sheet", measurements=[_m("Glucose", f"Resultados{PATH_SEP}Bioquímica", 90)])
    drop_wrappers(sheet)
    assert sheet.measurements[0].group_path == "Bioquímica"                       # same rule as topics, any layout


def test_a_provider_is_learnt_once_from_the_confirmed_names():
    from app.categories.health.outlines import known_provider
    hsm = "Hospital de Santa Maria, Unidade Local de Saúde de Santa Maria, E.P.E."
    lab = "Laboratório de Análises Clínicas Cintramédica"
    read = ("Laboratório de Hemodinâmica Cerebral, Serviço de Neurologia, Unidade Local de Saúde de Santa Maria, "
            "E.P.E. - Hospital de Santa Maria")
    assert known_provider(read, [hsm, lab]) == hsm
    assert known_provider("Cintramédica Mafra", [hsm, lab]) == lab
    assert known_provider("Hospital da Luz", [hsm, lab]) is None
    assert known_provider("Hospital de Santa Maria", ["Hospital de Santa Maria", "Clínica Santa Maria"]) is None  # two fit


def test_a_reference_cut_short_is_completed_from_the_other_reading():
    from app.categories.health.reading import complete_from
    whole = "< 4,00 (Nota:Sensibilidade analítica 0,006ng/mL)"
    x = ExtractedHealth(measurements=[_m("PSA total", "Marcadores", 0.5, "< 4,00", "ng/mL")])
    y = ExtractedHealth(measurements=[_m("PSA total", "Marcadores", 0.5, whole, "ng/mL")])
    complete_from(x, y)
    assert x.measurements[0].reference_text == whole
    z = ExtractedHealth(measurements=[_m("PSA total", "Marcadores", 0.5, "< 2,50", "ng/mL")])
    complete_from(z, y)
    assert z.measurements[0].reference_text == "< 2,50"                 # a different reference is not "completed"
