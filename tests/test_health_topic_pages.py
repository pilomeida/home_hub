"""The pages of Phase 1B: the topics baseline, one person's thread through a topic, and a written report's outline on
the paper's own page. Reports are filed through the production path (the reader's reply -> store.apply_extraction)."""

import asyncio
import json
import re
from types import SimpleNamespace

from sqlmodel import select

from app.domains.health import extraction as ex
from app.domains.health import store
from app.domains.health.text import PATH_SEP
from app.models.domain import Domain
from app.models.health import HealthEntry, HealthTopic
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.knowledge_factories import make_document
from tests.test_health_extraction import _png

EIXO = ["Conclusões", "Triplex carotídeo", "Eixo Carotídeo direito"]


def _reply(person, when, sections, provider="Hospital de Santa Maria", layout="report", category="imaging_report"):
    return {"category": category, "person_name": person, "document_date": when, "provider": provider,
            "record_kind": "test", "summary": "s", "layout": layout, "sections": sections}


def _section(path, *results):
    return {"path": path, "results": list(results)}


def _prose(text, test="Conclusão"):
    return {"test": test, "value_text": text}


def _num(test, value, unit="cm/s", reference="50 - 100"):
    return {"test": test, "value": value, "unit": unit, "reference": reference}


def _file(session, tmp_path, reply, name="r.pdf", notes=()):
    """File one paper the way production does: the reader's reply is parsed, then stored."""
    got = asyncio.run(ex.extract_health_document(
        _png(tmp_path), None, gateway=FakeGateway([gateway_text_result(json.dumps(reply))])))
    got.check_notes = list(notes)
    document = make_document(session, domain=Domain.HEALTH, category=reply["category"], filename=name)
    entry = store.apply_extraction(session, document, got)
    return document, entry


def _topic(session, name):
    return session.exec(select(HealthTopic).where(HealthTopic.name == name)).one()


def _two_reports(session, tmp_path):
    a = _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [
        _section(EIXO, _prose("Permeabilidade do eixo em 2023."), _num("Velocidade", 80))]), "a.pdf")
    b = _file(session, tmp_path, _reply("Maria Santos Almeida", "2025-06-01", [
        _section(EIXO, _prose("Sem alterações do eixo em 2025."), _num("Velocidade", 85))], provider="Clínica Norte"), "b.pdf")
    return a, b


# --- 1. the baseline -------------------------------------------------------------------------------------------

def test_the_topics_page_lists_proposed_topics_as_a_tree_and_approving_marks_one(client, session, tmp_path):
    _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [_section(EIXO, _prose("x"))]))
    html = client.get("/health/topics").text
    assert html.index("Triplex carotídeo") < html.index("Eixo Carotídeo direito")
    assert "Conclusões" not in html                                   # a wrapper heading is not a topic
    assert "proposed" in html and "Approve" in html
    parent, child = _topic(session, "Triplex carotídeo"), _topic(session, "Eixo Carotídeo direito")
    assert f"/health/topics/{child.id}" in html                       # each topic links to its thread
    indents = [int(x) for x in re.findall(r'<td style="padding-left:(\d+)px;">', html)]
    assert len(indents) == 2 and indents[1] > indents[0]              # the child sits deeper than its parent

    r = client.post(f"/health/topics/{parent.id}/approve", follow_redirects=False)
    assert r.status_code == 303
    html = client.get("/health/topics").text
    assert "approved" in html
    assert html.count(f'/health/topics/{parent.id}/approve') == 0 and html.count(f'/health/topics/{child.id}/approve') == 1


def test_a_topic_name_is_escaped_on_the_topics_page(client, session, tmp_path):
    _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [_section(["Eixo <u>x</u>"], _prose("y"))]))
    html = client.get("/health/topics").text
    assert "&lt;u&gt;x&lt;/u&gt;" in html and "<u>x</u>" not in html


def test_renaming_and_the_errors_of_the_topics_page_say_what_why_and_what_to_do(client, session, tmp_path):
    _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [_section(EIXO, _prose("x"))]))
    parent = _topic(session, "Triplex carotídeo")
    r = client.post(f"/health/topics/{parent.id}/rename", data={"name": "Eco-Doppler carotídeo"}, follow_redirects=False)
    assert r.status_code == 303 and "Eco-Doppler carotídeo" in client.get("/health/topics").text
    r = client.post(f"/health/topics/{parent.id}/rename", data={"name": "  "})
    assert r.status_code == 400 and "Not renamed." in r.text and "Type a name and try again." in r.text
    r = client.post("/health/topics/9999/approve")
    assert r.status_code == 404 and "Not approved." in r.text and "Reload the page" in r.text


# --- 2. "looks like" and merging ---------------------------------------------------------------------------------

def test_a_lookalike_topic_offers_the_merge_and_a_merge_moves_its_rows_to_the_thread(client, session, tmp_path):
    first, _ = _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [
        _section(EIXO, _prose("Prosa do primeiro eixo."))]), "a.pdf")
    _, entry = _file(session, tmp_path, _reply("Maria Santos Almeida", "2024-04-01", [
        _section(["Ecografia", "Eixo Carotideo direito"], _prose("Prosa do eixo sem acento."))]), "b.pdf")
    keep, twin = _topic(session, "Eixo Carotídeo direito"), _topic(session, "Eixo Carotideo direito")
    assert twin.similar_to_id == keep.id

    html = client.get("/health/topics").text
    assert "looks like Eixo Carotídeo direito" in html and "Merge?" in html

    r = client.post(f"/health/topics/{twin.id}/merge", data={"into": ""})
    assert r.status_code == 400
    assert "Not merged." in r.text and "Nothing was chosen to merge into." in r.text and "press Merge" in r.text
    assert 'value="Eixo Carotideo direito"' in client.get("/health/topics").text            # nothing changed

    r = client.post(f"/health/topics/{twin.id}/merge", data={"into": str(keep.id)}, follow_redirects=False)
    assert r.status_code == 303
    html = client.get("/health/topics").text
    assert 'value="Eixo Carotideo direito"' not in html and "looks like" not in html
    thread = client.get(f"/health/topics/{keep.id}?person={entry.person_id}").text
    assert "Prosa do primeiro eixo." in thread and "Prosa do eixo sem acento." in thread


def test_merging_a_topic_into_itself_gives_the_three_part_message(client, session, tmp_path):
    _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [_section(EIXO, _prose("x"))]))
    topic = _topic(session, "Triplex carotídeo")
    r = client.post(f"/health/topics/{topic.id}/merge", data={"into": str(topic.id)})
    assert r.status_code == 400 and "Not merged." in r.text and "cannot be merged into itself" in r.text


# --- 3. the thread -------------------------------------------------------------------------------------------------

def test_the_thread_shows_one_persons_conclusions_newest_first_and_a_metrics_table(client, session, tmp_path):
    (da, ea), (db, eb) = _two_reports(session, tmp_path)
    _file(session, tmp_path, _reply("Pedro Nuno dos Santos Almeida", "2024-01-01", [
        _section(EIXO, _prose("Conclusão que é do Pedro."), _num("Velocidade", 70))]), "p.pdf")
    topic = _topic(session, "Eixo Carotídeo direito")

    html = client.get(f"/health/topics/{topic.id}?person={ea.person_id}").text
    assert "Triplex carotídeo › Eixo Carotídeo direito" in html
    assert html.index("Sem alterações do eixo em 2025.") < html.index("Permeabilidade do eixo em 2023.")
    assert "Conclusão que é do Pedro." not in html
    assert "Clínica Norte" in html and "Hospital de Santa Maria" in html
    assert f"/health/documents/{da.id}" in html and f"/health/documents/{db.id}" in html
    assert "Conclusions over time" in html and "Metrics" in html
    metrics = html.split("Metrics", 1)[1]
    assert "Velocidade" in metrics and "(cm/s)" in metrics
    cells = re.findall(r"<td>([^<]*)</td>", metrics)                         # date, value, date, value: Pedro's 70 is not here
    assert cells == ["2023-03-26", "80 cm/s", "2025-06-01", "85 cm/s"]


def test_the_person_picker_defaults_to_the_one_with_most_rows_and_links_the_others(client, session, tmp_path):
    _two_reports(session, tmp_path)
    _, pedro = _file(session, tmp_path, _reply("Pedro Nuno dos Santos Almeida", "2024-01-01", [
        _section(EIXO, _prose("Conclusão que é do Pedro."))]), "p.pdf")
    topic = _topic(session, "Eixo Carotídeo direito")
    html = client.get(f"/health/topics/{topic.id}").text                    # no person: Maria has the most rows
    assert "Sem alterações do eixo em 2025." in html and "Conclusão que é do Pedro." not in html
    assert f"/health/topics/{topic.id}?person={pedro.person_id}" in html     # the other person is one link away
    html = client.get(f"/health/topics/{topic.id}?person={pedro.person_id}").text
    assert "Conclusão que é do Pedro." in html and "Sem alterações do eixo em 2025." not in html


def test_the_thread_carries_the_sub_topics_with_their_sub_headings_in_order(client, session, tmp_path):
    _, entry = _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [
        _section(["Conclusões", "Triplex carotídeo"], _prose("Primeiro geral do triplex.")),
        _section(EIXO, _prose("Prosa do eixo.")),
        _section(["Conclusões", "Triplex carotídeo"], _prose("Depois geral do triplex."))]))
    parent = _topic(session, "Triplex carotídeo")
    html = client.get(f"/health/topics/{parent.id}?person={entry.person_id}").text
    body = html.split("Conclusions over time", 1)[1].split("<h2>Metrics</h2>", 1)[0]
    seen = [("h4" if h4 else "p", (h4 or p).strip()) for h4, p in re.findall(r"<h4[^>]*>(.*?)</h4>|<p[^>]*>(.*?)</p>", body, re.S)]
    # the topic's own prose is not filed under the sub-topic's heading, before or after it
    assert seen == [("p", "Primeiro geral do triplex."), ("h4", "Eixo Carotídeo direito"), ("p", "Prosa do eixo."),
                    ("h4", "Triplex carotídeo"), ("p", "Depois geral do triplex.")]


def test_an_unknown_topic_is_a_404_with_what_why_and_what_to_do(client):
    r = client.get("/health/topics/9999")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["what"] and detail["why"] and detail["next_step"]


def test_a_topic_without_rows_says_so(client, session):
    topic = HealthTopic(name="Vazio", match_key="vazio")
    session.add(topic)
    session.commit()
    session.refresh(topic)
    r = client.get(f"/health/topics/{topic.id}")
    assert r.status_code == 200 and "Nothing has been filed under this topic yet." in r.text


# --- 4. the paper's page ---------------------------------------------------------------------------------------------

def test_a_reports_page_shows_its_outline_with_prose_under_the_right_heading_and_links_the_topics(client, session, tmp_path):
    doc, entry = _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [
        _section(EIXO, _prose("Prosa do eixo direito.")),
        _section(["Conclusões", "ECO/DTC"], _prose("Prosa do eco."))]))
    html = client.get(f"/health/documents/{doc.id}").text
    assert "Written report" in html
    outline = html.split("Outline", 1)[1]
    assert (outline.index("Triplex carotídeo") < outline.index("Eixo Carotídeo direito") < outline.index("Prosa do eixo direito.")
            < outline.index("ECO/DTC") < outline.index("Prosa do eco."))
    eixo, triplex, eco = _topic(session, "Eixo Carotídeo direito"), _topic(session, "Triplex carotídeo"), _topic(session, "ECO/DTC")
    for topic in (eixo, triplex, eco):                                         # every heading leads to its own topic
        assert f'href="/health/topics/{topic.id}?person={entry.person_id}"' in html
    assert "This report prints no numbers." in html
    assert "<th>Test</th>" not in html                                         # the sheet's results table is not used
    assert "Why this was done" in html                                         # the Phase 1 box is intact


def test_the_metrics_table_appears_only_when_the_report_has_numbers(client, session, tmp_path):
    doc, _ = _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [
        _section(EIXO, _prose("Prosa."), _num("Velocidade", 80)),
        _section(["Conclusões", "ECO/DTC"], _num("Índice de pulsatilidade", 0.9, unit=""))]))
    html = client.get(f"/health/documents/{doc.id}").text
    assert "This report prints no numbers." not in html
    metrics = html.split("Metrics", 1)[1]
    assert "Velocidade" in metrics and "80 cm/s" in metrics and "50 - 100" in metrics
    assert "Índice de pulsatilidade" in metrics and "0.9" in metrics


def test_the_outline_looks_right_button_confirms_and_then_says_so(client, session, tmp_path):
    doc, entry = _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [_section(EIXO, _prose("x"))]))
    html = client.get(f"/health/documents/{doc.id}").text
    assert "The outline looks right" in html and "Outline confirmed" not in html
    r = client.post(f"/health/documents/{doc.id}/outline/ok", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/health/documents/{doc.id}"
    html = client.get(f"/health/documents/{doc.id}").text
    assert "Outline confirmed" in html and "The outline looks right" not in html
    session.refresh(entry)
    assert entry.outline_confirmed_at is not None


def test_confirming_an_outline_that_cannot_be_kept_gives_the_three_part_detail(client, session, tmp_path):
    doc, _ = _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [_section(EIXO, _prose("x"))], provider=""))
    r = client.post(f"/health/documents/{doc.id}/outline/ok")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["what"] == "Outline not confirmed." and detail["why"] and detail["next_step"]
    other = make_document(session, domain=Domain.HOUSE, category="house_appliance")
    assert client.post(f"/health/documents/{other.id}/outline/ok").status_code == 404


def test_a_sheet_still_shows_its_results_table_and_no_report_line(client, session, tmp_path):
    doc, _ = _file(session, tmp_path, _reply("Maria Santos Almeida", "2026-03-12", [
        _section(["Bioquímica"], {"test": "Glucose", "value": 90, "unit": "mg/dL", "reference": "70 - 99"})],
        layout="sheet", category="lab_result"))
    html = client.get(f"/health/documents/{doc.id}").text
    assert "Written report" not in html and "The outline looks right" not in html
    assert "<th>Test</th>" in html and "Glucose" in html and "90 mg/dL" in html and "Bioquímica" in html


def test_text_from_the_paper_is_escaped_on_the_paper_and_the_thread_pages(client, session, tmp_path):
    doc, entry = _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [
        _section(["Conclusões", "Eixo <i>x</i>"], _prose("Veja <b>negrito</b> aqui."))]))
    topic = _topic(session, "Eixo <i>x</i>")
    for url in (f"/health/documents/{doc.id}", f"/health/topics/{topic.id}?person={entry.person_id}"):
        html = client.get(url).text
        assert "&lt;b&gt;negrito&lt;/b&gt;" in html and "<b>negrito</b>" not in html
        assert "&lt;i&gt;x&lt;/i&gt;" in html and "<i>x</i>" not in html


def test_the_health_menu_links_to_the_topics_page():
    from app.domains.health.spec import SPEC
    assert ("Topics", "/health/topics") in [(link.label, link.url) for link in SPEC.nav_links]


def test_a_parent_heading_links_nowhere_after_a_merge_but_the_leaf_follows_to_the_target(client, session, tmp_path):
    doc, entry = _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [
        _section(["Ecografia", "Eixo Carotídeo direito"], _prose("Prosa da ecografia."))]), "a.pdf")
    _file(session, tmp_path, _reply("Maria Santos Almeida", "2024-04-01", [
        _section(["Outro", "Eixo Carotideo direito"], _prose("Prosa do outro."))]), "b.pdf")
    source = _topic(session, "Eixo Carotídeo direito")
    target = _topic(session, "Eixo Carotideo direito")
    assert target.parent_id == _topic(session, "Outro").id
    r = client.post(f"/health/topics/{source.id}/merge", data={"into": str(target.id)}, follow_redirects=False)
    assert r.status_code == 303
    html = client.get(f"/health/documents/{doc.id}").text
    outline = html.split("<h2>Outline</h2>", 1)[1]
    assert re.search(r"<h3[^>]*>\s*Ecografia\s*</h3>", outline)                       # printed, but not a link
    assert f'href="/health/topics/{target.id}?person={entry.person_id}"' in outline    # the leaf follows the merge
    assert f'/health/topics/{_topic(session, "Outro").id}' not in outline              # "Outro" was never printed here


def test_the_picker_leaves_out_a_person_who_only_has_earlier_restated_results(client, session, tmp_path):
    _, maria = _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [_section(EIXO, _prose("Prosa da Maria."))]))
    _file(session, tmp_path, _reply("Pedro Nuno dos Santos Almeida", "2024-01-01", [
        _section(["Outro"], _prose("Outra coisa do Pedro.")),
        _section(EIXO, {"test": "Velocidade", "value_text": "só antes", "previous": [{"value": 70, "date": "2020-01-01"}]})]))
    from app.models.health import HealthMeasurement
    for m in session.exec(select(HealthMeasurement).where(HealthMeasurement.test == "Velocidade")).all():
        if not m.is_previous:
            session.delete(m)
    session.commit()
    topic = _topic(session, "Eixo Carotídeo direito")
    html = client.get(f"/health/topics/{topic.id}").text
    assert "Prosa da Maria." in html and "Pedro Nuno" not in html and "?person=" not in html


def test_the_notes_box_does_not_claim_the_paper_has_text_to_compare_with(client, session, tmp_path):
    doc, _ = _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [_section(EIXO, _prose("x"))]),
                   notes=["Only one of two readings found: Velocidade. Look at the paper."])
    html = client.get(f"/health/documents/{doc.id}").text
    assert "The Hub checked its reading (against the paper's own text, or by reading it twice) and could not settle:" in html
    assert "compared what it read with the paper" not in html
    assert "Only one of two readings found: Velocidade. Look at the paper." in html


def test_confirming_an_outline_records_who_did_it_when_the_hub_knows(session, tmp_path):
    from app.routers import health as health_router
    doc, entry = _file(session, tmp_path, _reply("Maria Santos Almeida", "2023-03-26", [_section(EIXO, _prose("x"))]))
    signed_in = SimpleNamespace(state=SimpleNamespace(user_email="rute@example.test"))
    asyncio.run(health_router.outline_ok(signed_in, doc.id, session))
    session.refresh(entry)
    assert entry.outline_confirmed_by == "rute@example.test" and entry.outline_confirmed_at is not None
    other, entry2 = _file(session, tmp_path, _reply("Maria Santos Almeida", "2024-03-26", [_section(EIXO, _prose("y"))]), "o.pdf")
    asyncio.run(health_router.outline_ok(SimpleNamespace(state=SimpleNamespace()), other.id, session))
    session.refresh(entry2)
    assert entry2.outline_confirmed_by is None and entry2.outline_confirmed_at is not None
