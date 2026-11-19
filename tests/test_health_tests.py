"""PLAN step 5b: a lab result's wording is matched to a known test, so the same test from two labs is one line."""

from datetime import date

from sqlmodel import select

from app.categories.health import tests as t
from app.categories.health.extraction import ExtractedHealth, ExtractedMeasurement
from app.categories.health.store import apply_extraction, entry_for
from app.categories.health.text import PATH_SEP
from app.models.category import Category
from app.models.health import HealthMeasurement, HealthTest
from app.services import review
from tests.knowledge_factories import make_document


def _m(test, value, unit, path="Bioquímica", previous=False, when=None, reference=None):
    return ExtractedMeasurement(test, value, None, unit, None, None, when, False, "", group_path=path,
                                is_previous=previous, reference_text=reference)


def _sheet(session, person, when, provider, measurements, name):
    doc = make_document(session, category=Category.HEALTH, paper_kind="lab_result", filename=name)
    apply_extraction(session, doc, ExtractedHealth(person_name=person, document_date=when, provider=provider,
                                                   record_kind="test", summary="s", layout="sheet",
                                                   measurements=measurements))
    return doc, entry_for(session, doc.id)


def test_units_and_wordings_fold():
    assert t.unit_key("µmol/L") == t.unit_key("umol/l")
    assert t.unit_key("x10^9/L") == t.unit_key("10⁹/L") == t.unit_key("10^9/l")
    assert t.wording("Resultado (IFCC)", f"Bioquímica{PATH_SEP}Hemoglobina glicada A1c") == "Hemoglobina glicada A1c Resultado (IFCC)"
    assert t.wording("CREATININA (Mét.Jaffé cinético)", "Bioquímica") == "CREATININA"


def test_the_same_blood_test_from_two_labs_lands_on_one_line(session):
    _, a = _sheet(session, "Rute Almeida", date(2024, 11, 5), "Cintramédica",
                  [_m("Glicose", 92, "mg/dL"), _m("Hemoglobina", 13.4, "g/dL", "Hemograma")], "a.pdf")
    _, b = _sheet(session, "Rute Almeida", date(2026, 3, 1), "Unilabs",
                  [_m("GLICOSE", 101, "mg/dl", "Química clínica"), _m("Hemoglobina", 13.9, "g/dL", "Eritrograma")], "b.pdf")
    lines = {l["test"].name: l for l in t.results_of(session, a.person_id)}
    assert set(lines) == {"Glicose", "Hemoglobina"}
    assert [p["value"] for p in lines["Glicose"]["points"]] == [101, 92]       # newest first, two labs, one line
    assert lines["Hemoglobina"]["papers"] == 2


def test_a_near_wording_in_the_same_unit_joins_and_a_different_unit_never_does(session):
    _, a = _sheet(session, "Rute Almeida", date(2024, 1, 1), "Lab A",
                  [_m("Neutrófilos", 55, "%"), _m("Colesterol total", 180, "mg/dL")], "a.pdf")
    _sheet(session, "Rute Almeida", date(2025, 1, 1), "Lab B",
           [_m("Neutrofilos", 3.1, "10^9/L"), _m("Colesterol Total.", 190, "mg/dL")], "b.pdf")
    names = sorted((l["test"].name, l["test"].unit_key, len(l["points"])) for l in t.results_of(session, a.person_id))
    assert names == [("Colesterol total", "mg/dl", 2), ("Neutrofilos", "10^9/l", 1), ("Neutrófilos", "%", 1)]


def test_a_new_test_waits_for_a_person_and_same_as_merges_it(session):
    _, a = _sheet(session, "Rute Almeida", date(2024, 1, 1), "Lab A", [_m("Ferritina sérica", 40, "ng/mL")], "a.pdf")
    _sheet(session, "Rute Almeida", date(2025, 1, 1), "Lab B", [_m("Ferritina", 55, "ng/mL")], "b.pdf")
    tests = session.exec(select(HealthTest).order_by(HealthTest.id)).all()
    assert [x.name for x in tests] == ["Ferritina sérica", "Ferritina"] and tests[1].similar_to_id == tests[0].id
    assert tests[0].approved and not tests[1].approved      # only the one that looks like another is asked
    items = {i.ref: i for i in review.waiting(session, area="health")}
    assert f"health_test:{tests[0].id}" not in items
    second = items[f"health_test:{tests[1].id}"]
    assert [o["value"] for o in review.options(second)] == ["same", "approve"]
    review.decide(session, second.id, "same", by="Rute")
    lines = t.results_of(session, a.person_id)
    assert len(lines) == 1 and [p["value"] for p in lines[0]["points"]] == [55, 40]
    _sheet(session, "Rute Almeida", date(2026, 1, 1), "Lab B", [_m("Ferritina", 61, "ng/mL")], "c.pdf")
    assert len(session.exec(select(HealthTest)).all()) == 2                      # the merged wording now finds it
    assert [p["value"] for p in t.results_of(session, a.person_id)[0]["points"]] == [61, 55, 40]


def test_merging_across_units_is_refused_with_a_reason(session):
    _sheet(session, "Rute Almeida", date(2024, 1, 1), "Lab A", [_m("Ureia", 30, "mg/dL"), _m("Ureia", 5, "mmol/L")], "a.pdf")
    one, two = session.exec(select(HealthTest).order_by(HealthTest.id)).all()
    try:
        t.merge_tests(session, two.id, one.id)
        raise AssertionError("merged across units")
    except t.TestError as exc:
        assert exc.problem.what == "Not merged." and "mmol/L" in exc.problem.why and exc.problem.next_step


def test_an_earlier_result_printed_on_a_later_paper_is_one_point(session):
    _, a = _sheet(session, "Rute Almeida", date(2024, 1, 1), "Lab A", [_m("Glicose", 92, "mg/dL")], "a.pdf")
    _sheet(session, "Rute Almeida", date(2025, 1, 1), "Lab A",
           [_m("Glicose", 99, "mg/dL"), _m("Glicose", 92, "mg/dL", previous=True, when=date(2024, 1, 1))], "b.pdf")
    assert [p["value"] for p in t.results_of(session, a.person_id)[0]["points"]] == [99, 92]


def test_a_written_report_gets_no_tests(session):
    doc = make_document(session, category=Category.HEALTH, paper_kind="imaging_report", filename="r.pdf")
    apply_extraction(session, doc, ExtractedHealth(person_name="Rute Almeida", document_date=date(2024, 1, 1),
                                                   summary="s", layout="report",
                                                   measurements=[_m("Conclusão", None, None, "Eixo")]))
    assert session.exec(select(HealthTest)).all() == []
    assert session.exec(select(HealthMeasurement.test_id)).all() == [None]


def test_the_results_page_shows_one_line_per_test(client, session):
    _, a = _sheet(session, "Rute Almeida", date(2024, 11, 5), "Lab A", [_m("Glicose", 92, "mg/dL")], "a.pdf")
    _sheet(session, "Rute Almeida", date(2026, 3, 1), "Lab B", [_m("GLICOSE", 101, "mg/dl")], "b.pdf")
    html = client.get(f"/health/people/{a.person_id}/results").text
    assert html.count("<td>Glicose") == 1 and "101" in html and "2024-11-05" in html
    r = client.get("/health/people/9999/results")
    assert r.status_code == 404 and r.json()["detail"]["next_step"]
