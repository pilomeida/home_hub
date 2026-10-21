from datetime import date

import pytest
from sqlmodel import select

from app.domains.health import topics as t
from app.domains.health.extraction import ExtractedHealth, ExtractedMeasurement
from app.domains.health.store import apply_extraction, entry_for
from app.domains.health.text import PATH_SEP
from app.models.domain import Domain
from app.models.health import HealthMeasurement, HealthTopic
from tests.knowledge_factories import make_document

CAROTID = f"Triplex carotídeo{PATH_SEP}Eixo Carotídeo direito"


def _m(test, value, path, when, previous=False, unit="mm/s"):
    return ExtractedMeasurement(test, value, None if value is not None else "normal", unit, None, None, when, False, "",
                                group_path=path, is_previous=previous)


def _report(session, person, when, measurements, name="r.pdf"):
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result", filename=name)
    ex = ExtractedHealth(person_name=person, document_date=when, provider="Clinic", record_kind="test", summary="s",
                         measurements=measurements)
    apply_extraction(session, doc, ex)
    entry = entry_for(session, doc.id)
    entry.layout = "report"
    session.add(entry)
    session.commit()
    t.attach_topics(session, entry)
    return doc, entry


def _topics(session):
    return session.exec(select(HealthTopic).order_by(HealthTopic.id)).all()


def test_topic_path_drops_leading_wrappers_only():
    assert t.topic_path(f"Conclusões{PATH_SEP}{CAROTID}") == ["Triplex carotídeo", "Eixo Carotídeo direito"]
    assert t.topic_path("Triplex carotídeo") == ["Triplex carotídeo"]
    assert t.topic_path(f"A{PATH_SEP}Conclusões") == ["A", "Conclusões"]
    assert t.topic_path("") == []
    assert t.topic_path("Conclusões") == []


def test_resolve_topic_creates_unapproved_and_then_finds_the_same_rows(session):
    leaf = t.resolve_topic(session, ["Triplex carotídeo", "Eixo Carotídeo direito"])
    assert [(x.name, x.approved) for x in _topics(session)] == [("Triplex carotídeo", False), ("Eixo Carotídeo direito", False)]
    assert leaf.parent_id == _topics(session)[0].id
    again = t.resolve_topic(session, ["triplex carotideo", "Eixo carotideo direito"])
    assert again.id == leaf.id and len(_topics(session)) == 2
    assert t.resolve_topic(session, []) is None


def test_the_same_chapter_on_a_later_paper_shares_a_topic_and_the_thread_joins_them(session):
    _, e1 = _report(session, "Maria Santos Almeida", date(2023, 5, 1), [_m("Velocidade", 80.0, CAROTID, date(2023, 5, 1))], "a.pdf")
    _, e2 = _report(session, "Maria Santos Almeida", date(2025, 6, 1), [_m("Velocidade", 85.0, f"Conclusões{PATH_SEP}{CAROTID}", date(2025, 6, 1))], "b.pdf")
    ids = {m.topic_id for m in session.exec(select(HealthMeasurement)).all()}
    assert len(ids) == 1 and None not in ids
    th = t.thread(session, ids.pop(), e1.person_id)
    assert [x["entry"].id for x in th["entries"]] == [e2.id, e1.id]
    assert th["path"] == CAROTID and th["topic"].name == "Eixo Carotídeo direito"


def test_attach_topics_leaves_a_sheet_alone_and_gives_no_topic_to_an_empty_path(session):
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result", filename="s.pdf")
    apply_extraction(session, doc, ExtractedHealth(person_name="Ana Lima", document_date=date(2026, 1, 1), summary="s",
                                                   measurements=[_m("LDL", 1.0, "Lípidos", date(2026, 1, 1))]))
    entry = entry_for(session, doc.id)
    t.attach_topics(session, entry)
    assert _topics(session) == []
    _, rep = _report(session, "Ana Lima", date(2026, 2, 1), [_m("LDL", 1.0, "", date(2026, 2, 1)),
                                                              _m("Nota", None, "Conclusões", date(2026, 2, 1))], "r.pdf")
    assert [m.topic_id for m in session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == rep.id)).all()] == [None, None]


def test_similar_to_points_at_a_topic_with_the_same_key_elsewhere_and_not_at_a_different_one(session):
    first = t.resolve_topic(session, ["Triplex carotídeo", "Eixo Carotídeo direito"])
    other = t.resolve_topic(session, ["Ecografia", "Eixo Carotideo direito"])
    assert other.similar_to_id == first.id
    different = t.resolve_topic(session, ["Ecografia", "Fígado"])
    assert different.similar_to_id is None
    assert first.similar_to_id is None


def test_merge_moves_measurements_and_merges_children(session):
    _, e1 = _report(session, "Maria Santos Almeida", date(2023, 5, 1),
                    [_m("Velocidade", 80.0, f"Carótidas{PATH_SEP}Direito", date(2023, 5, 1)),
                     _m("Espessura", 0.8, f"Carótidas{PATH_SEP}Esquerdo", date(2023, 5, 1))], "a.pdf")
    _, e2 = _report(session, "Maria Santos Almeida", date(2025, 5, 1),
                    [_m("Velocidade", 82.0, f"Triplex carotídeo{PATH_SEP}Direito", date(2025, 5, 1))], "b.pdf")
    src = t.resolve_topic(session, ["Carótidas"])
    tgt = t.resolve_topic(session, ["Triplex carotídeo"])
    src_dir = t.resolve_topic(session, ["Carótidas", "Direito"])
    tgt_dir = t.resolve_topic(session, ["Triplex carotídeo", "Direito"])
    src_esq = t.resolve_topic(session, ["Carótidas", "Esquerdo"])
    t.merge_topics(session, src.id, tgt.id)
    session.expire_all()
    assert session.get(HealthTopic, src.id).merged_into_id == tgt.id and session.get(HealthTopic, src.id).approved
    assert session.get(HealthTopic, src_dir.id).merged_into_id == tgt_dir.id
    assert session.get(HealthTopic, src_esq.id).parent_id == tgt.id and session.get(HealthTopic, src_esq.id).merged_into_id is None
    by_topic = {m.test: m.topic_id for m in session.exec(select(HealthMeasurement)).all()}
    assert by_topic["Espessura"] == src_esq.id
    assert {m.topic_id for m in session.exec(select(HealthMeasurement).where(HealthMeasurement.test == "Velocidade")).all()} == {tgt_dir.id}
    # a later paper written the old way follows the merge
    assert t.resolve_topic(session, ["Carótidas", "Direito"]).id == tgt_dir.id
    th = t.thread(session, tgt.id, e1.person_id)
    assert {i.test for x in th["entries"] for i in x["items"]} == {"Velocidade", "Espessura"}


def _refused(call):
    with pytest.raises(t.TopicError) as err:
        call()
    p = err.value.problem
    assert p.what and p.why and p.next_step
    return err.value


def test_merge_refuses_itself_and_an_already_merged_topic(session):
    a, b, c = (t.resolve_topic(session, [n]) for n in ("A", "B", "C"))
    _refused(lambda: t.merge_topics(session, a.id, a.id))
    t.merge_topics(session, a.id, b.id)
    _refused(lambda: t.merge_topics(session, a.id, c.id))
    _refused(lambda: t.merge_topics(session, c.id, a.id))
    _refused(lambda: t.merge_topics(session, c.id, 9999))


def test_rename_refuses_empty_and_a_sibling_clash_but_allows_the_same_name_elsewhere(session):
    a = t.resolve_topic(session, ["Fígado", "Lobo direito"])
    b = t.resolve_topic(session, ["Fígado", "Lobo esquerdo"])
    c = t.resolve_topic(session, ["Rim", "Lobo esquerdo"])
    _refused(lambda: t.rename_topic(session, a.id, "  "))
    _refused(lambda: t.rename_topic(session, a.id, "lobo esquerdo"))
    t.rename_topic(session, c.id, "Lobo direito")
    t.rename_topic(session, a.id, "Lobo  Anterior")
    session.refresh(a)
    assert a.name == "Lobo Anterior" and a.match_key == "lobo anterior"
    assert b.name == "Lobo esquerdo"


def test_names_for_prompt_lists_only_approved_unmerged_topics_as_paths(session):
    leaf = t.resolve_topic(session, ["Triplex carotídeo", "Eixo Carotídeo direito"])
    t.resolve_topic(session, ["Zeta"])
    assert t.names_for_prompt(session) == []
    t.approve_topic(session, leaf.parent_id)
    t.approve_topic(session, leaf.id)
    assert t.names_for_prompt(session) == ["Triplex carotídeo", CAROTID]
    other = t.resolve_topic(session, ["Outro"])
    t.approve_topic(session, other.id)
    t.merge_topics(session, other.id, leaf.id)
    assert t.names_for_prompt(session) == ["Triplex carotídeo", CAROTID]
    assert t.names_for_prompt(session, limit=1) == ["Triplex carotídeo"]
    _refused(lambda: t.approve_topic(session, 9999))


def test_thread_is_per_person_numeric_metrics_only_and_previous_rows_are_earlier_points(session):
    path = f"Triplex{PATH_SEP}Direito"
    _, mine = _report(session, "Maria Santos Almeida", date(2025, 6, 1),
                      [_m("Velocidade", 85.0, path, date(2025, 6, 1)),
                       _m("Velocidade", 80.0, path, date(2023, 5, 1), previous=True),
                       _m("Aspeto", None, path, date(2025, 6, 1))], "a.pdf")
    _report(session, "Pedro Santos Almeida", date(2025, 6, 2), [_m("Velocidade", 999.0, path, date(2025, 6, 2))], "b.pdf")
    topic = t.resolve_topic(session, ["Triplex", "Direito"])
    th = t.thread(session, topic.parent_id, mine.person_id)           # asking at the parent includes descendants
    assert len(th["entries"]) == 1 and th["entries"][0]["entry"].id == mine.id
    items = th["entries"][0]["items"]
    assert {i.test for i in items} == {"Velocidade", "Aspeto"} and not any(i.is_previous for i in items)
    assert all(i.person_id == mine.person_id for i in items)
    assert len(th["metrics"]) == 1
    metric = th["metrics"][0]
    assert metric["label"] == "Velocidade" and metric["unit"] == "mm/s"
    assert metric["points"] == [(date(2023, 5, 1), 80.0), (date(2025, 6, 1), 85.0)]


def test_people_of_counts_each_persons_own_rows_follows_merges_and_skips_earlier_restated_results(session):
    _, ma = _report(session, "Maria Santos Almeida", date(2023, 5, 1), [_m("Velocidade", 80.0, CAROTID, date(2023, 5, 1))], "a.pdf")
    _report(session, "Maria Santos Almeida", date(2025, 6, 1), [_m("Velocidade", 85.0, CAROTID, date(2025, 6, 1))], "b.pdf")
    _, pe = _report(session, "Pedro Almeida", date(2024, 1, 1), [_m("Velocidade", 70.0, CAROTID, date(2024, 1, 1)),
                                                               _m("Velocidade", 60.0, CAROTID, date(2020, 1, 1), previous=True)], "c.pdf")
    _, ana = _report(session, "Ana Lima", date(2024, 2, 1), [_m("Velocidade", None, CAROTID, date(2024, 2, 1), previous=True)], "d.pdf")
    for m in session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == ana.id)).all():
        m.topic_id = session.exec(select(HealthMeasurement.topic_id).where(HealthMeasurement.entry_id == ma.id)).first()
        session.add(m)
    session.commit()
    topic_id = session.exec(select(HealthMeasurement.topic_id).where(HealthMeasurement.entry_id == ma.id)).first()
    assert t.people_of(session, topic_id) == [(ma.person_id, 2), (pe.person_id, 1)]     # Ana: earlier results only
    other = t.resolve_topic(session, ["Outro", "Lugar"])
    t.merge_topics(session, topic_id, other.id)
    assert t.people_of(session, topic_id) == t.people_of(session, other.id) == [(ma.person_id, 2), (pe.person_id, 1)]
    with pytest.raises(t.TopicError):
        t.people_of(session, 9999)
    assert t.thread(session, other.id, None)["entries"] == [] and t.thread(session, other.id, None)["path"] == f"Outro{PATH_SEP}Lugar"


# --- review fixes: one series per topic and metric, lookalikes, prompt names, live children ---------------------

def test_the_same_chapter_printed_wrapped_and_unwrapped_is_one_series(session):
    _, e1 = _report(session, "Maria Santos Almeida", date(2023, 5, 1),
                    [_m("Velocidade", 80.0, f"Conclusões{PATH_SEP}{CAROTID}", date(2023, 5, 1))], "a.pdf")
    _report(session, "Maria Santos Almeida", date(2025, 6, 1), [_m("Velocidade", 85.0, CAROTID, date(2025, 6, 1))], "b.pdf")
    leaf = t.resolve_topic(session, ["Triplex carotídeo", "Eixo Carotídeo direito"])
    th = t.thread(session, leaf.id, e1.person_id)
    assert len(th["metrics"]) == 1
    assert th["metrics"][0]["points"] == [(date(2023, 5, 1), 80.0), (date(2025, 6, 1), 85.0)]
    assert th["metrics"][0]["caption"] == ""                           # the thread's own topic: nothing below it to name


def test_a_series_is_captioned_with_the_sub_topic_below_the_thread_topic(session):
    _, e1 = _report(session, "Maria Santos Almeida", date(2023, 5, 1),
                    [_m("Velocidade", 80.0, f"Conclusões{PATH_SEP}{CAROTID}", date(2023, 5, 1))], "a.pdf")
    _report(session, "Maria Santos Almeida", date(2025, 6, 1), [_m("Velocidade", 85.0, CAROTID, date(2025, 6, 1))], "b.pdf")
    parent = t.resolve_topic(session, ["Triplex carotídeo"])
    th = t.thread(session, parent.id, e1.person_id)
    assert [(x["label"], x["caption"], len(x["points"])) for x in th["metrics"]] == [("Velocidade", "Eixo Carotídeo direito", 2)]


def test_after_a_merge_the_merged_topics_series_joins_the_targets(session):
    _, e1 = _report(session, "Maria Santos Almeida", date(2023, 5, 1),
                    [_m("Velocidade", 80.0, f"Carótidas{PATH_SEP}Direito", date(2023, 5, 1))], "a.pdf")
    _report(session, "Maria Santos Almeida", date(2025, 5, 1),
            [_m("Velocidade", 82.0, f"Triplex carotídeo{PATH_SEP}Direito", date(2025, 5, 1))], "b.pdf")
    src = t.resolve_topic(session, ["Carótidas"])
    tgt = t.resolve_topic(session, ["Triplex carotídeo"])
    t.merge_topics(session, src.id, tgt.id)
    th = t.thread(session, tgt.id, e1.person_id)
    assert len(th["metrics"]) == 1
    assert th["metrics"][0]["points"] == [(date(2023, 5, 1), 80.0), (date(2025, 5, 1), 82.0)]


def test_two_spellings_of_a_metric_that_differ_in_case_or_accents_are_one_series(session):
    _, e1 = _report(session, "Maria Santos Almeida", date(2023, 5, 1), [_m("Velocidade Sistólica", 80.0, CAROTID, date(2023, 5, 1))], "a.pdf")
    _report(session, "Maria Santos Almeida", date(2025, 6, 1), [_m("velocidade sistolica", 85.0, CAROTID, date(2025, 6, 1), unit="MM/S")], "b.pdf")
    leaf = t.resolve_topic(session, ["Triplex carotídeo", "Eixo Carotídeo direito"])
    th = t.thread(session, leaf.id, e1.person_id)
    assert len(th["metrics"]) == 1 and len(th["metrics"][0]["points"]) == 2
    assert th["metrics"][0]["label"] == "velocidade sistolica"          # named as the newest paper spells it


def test_numbered_siblings_are_not_offered_as_lookalikes_but_a_real_near_duplicate_is(session):
    first = t.resolve_topic(session, ["Segmento V1"])
    second = t.resolve_topic(session, ["Segmento V2"])
    assert second.similar_to_id is None and first.similar_to_id is None
    carotid = t.resolve_topic(session, ["Triplex carotídeo", "Eixo carotídeo direito"])
    twin = t.resolve_topic(session, ["Ecografia", "Eixo Carotídeo direito"])
    assert twin.similar_to_id == carotid.id


def test_the_prompt_names_a_leaf_only_when_every_ancestor_is_approved(session):
    leaf = t.resolve_topic(session, ["Triplex carotídeo", "Eixo Carotídeo direito"])
    t.approve_topic(session, leaf.id)                                  # the parent is still only proposed
    assert t.names_for_prompt(session) == []
    t.approve_topic(session, leaf.parent_id)
    assert t.names_for_prompt(session) == ["Triplex carotídeo", CAROTID]


def test_a_live_child_with_the_same_name_is_preferred_to_a_merged_one(session):
    parent = t.resolve_topic(session, ["Fígado"])
    old = t.resolve_topic(session, ["Fígado", "Lobo"])
    elsewhere = t.resolve_topic(session, ["Rim", "Lobo"])
    t.merge_topics(session, old.id, elsewhere.id)                      # the old Lobo under Fígado is now merged away
    fresh = HealthTopic(name="Lobo", match_key="lobo", parent_id=parent.id, approved=False)
    session.add(fresh)
    session.commit()
    session.refresh(fresh)
    assert t.resolve_topic(session, ["Fígado", "Lobo"]).id == fresh.id
    # with no live match, a merged child still leads to where it went
    only = t.resolve_topic(session, ["Pulmão", "Lobo"])
    t.merge_topics(session, only.id, elsewhere.id)
    assert t.resolve_topic(session, ["Pulmão", "Lobo"]).id == elsewhere.id
