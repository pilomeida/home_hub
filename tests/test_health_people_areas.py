import pytest

from app.domains.health import areas, people
from app.domains.health.text import fold, match_key
from app.models.document import Document
from app.models.health import HealthEntry, HealthEntryTag, HealthMeasurement
from tests.knowledge_factories import make_document
from app.models.domain import Domain


def _entry(session, person=None):
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result")
    e = HealthEntry(document_id=doc.id, person_id=person.id if person else None)
    session.add(e)
    session.commit()
    session.refresh(e)
    return e


def test_fold_and_match_key_ignore_case_accents_and_spacing():
    assert fold("João  Évora") == "joao  evora"
    assert match_key("  João   ÉVORA ") == "joao evora"


def test_a_person_is_discovered_once_then_matched_with_spelling_variants(session):
    a = people.find_or_create_person(session, "Maria Santos Almeida")
    assert a.confirmed is False
    assert people.find_or_create_person(session, "maria  santos almeida").id == a.id
    assert people.find_or_create_person(session, "Maria Santos").id == a.id       # a unique name-part match
    assert people.find_or_create_person(session, "Pedro Santos Almeida").id != a.id
    assert people.find_or_create_person(session, "  ") is None
    assert people.find_or_create_person(session, None) is None


def test_two_people_sharing_a_part_are_not_merged_automatically(session):
    people.find_or_create_person(session, "Ana Santos")
    people.find_or_create_person(session, "Rita Santos")
    c = people.find_or_create_person(session, "Santos")                            # one token: never auto-matched
    assert c.name == "Santos"


def test_merging_people_moves_everything_and_hides_the_duplicate_behind_an_alias(session):
    keep = people.find_or_create_person(session, "Maria Santos Almeida")
    dup = people.find_or_create_person(session, "Mariah Almeida")
    e = _entry(session, dup)
    session.add(HealthMeasurement(entry_id=e.id, person_id=dup.id, test="LDL", test_key="ldl"))
    session.commit()
    people.merge_people(session, dup.id, keep.id)
    session.refresh(e)
    assert e.person_id == keep.id
    session.refresh(dup)
    assert dup.merged_into_id == keep.id                                           # kept as an alias, not deleted
    assert [p.id for p in people.all_people(session)] == [keep.id]
    assert all(m.person_id == keep.id for m in session.query(HealthMeasurement).all())


def test_a_merged_away_spelling_still_resolves_to_the_person_it_was_merged_into(session):
    keep = people.find_or_create_person(session, "Pedro Santos Almeida")
    dup = people.find_or_create_person(session, "Rita Gomes")
    other = people.find_or_create_person(session, "Rita G.")
    people.merge_people(session, other.id, dup.id)
    people.merge_people(session, dup.id, keep.id)                                  # the alias of an alias moves too
    session.refresh(other)
    assert other.merged_into_id == keep.id
    assert people.find_or_create_person(session, "Rita Gomes").id == keep.id
    assert people.find_or_create_person(session, "RITA G.").id == keep.id
    assert [p.id for p in people.all_people(session)] == [keep.id]


def test_merging_into_or_from_a_merged_away_person_is_refused(session):
    a = people.find_or_create_person(session, "Ana Lima")
    b = people.find_or_create_person(session, "Bruno Costa")
    c = people.find_or_create_person(session, "Carla Dias")
    people.merge_people(session, a.id, b.id)
    with pytest.raises(people.PersonError) as exc:
        people.merge_people(session, c.id, a.id)
    assert exc.value.problem.what == "Not merged." and "already merged" in exc.value.problem.why


def test_an_area_is_proposed_unapproved_and_reused_by_name(session):
    eyes = areas.resolve_area(session, "Eyes", "area")
    assert eyes.approved is False
    assert areas.resolve_area(session, " eyes ", "area").id == eyes.id
    assert areas.resolve_area(session, "Eyes", "condition").id != eyes.id          # kinds are separate lists


def test_tags_attach_to_the_entry_and_replace_on_retag(session):
    e = _entry(session)
    areas.set_tags(session, e, ["Eyes", "Skin"], ["Glaucoma"])
    assert {a.name for a in areas.tags_of(session, e.id)} == {"Eyes", "Skin", "Glaucoma"}
    areas.set_tags(session, e, ["Eyes"], [])
    assert {a.name for a in areas.tags_of(session, e.id)} == {"Eyes"}


def test_merging_areas_repoints_tags_without_duplicates_and_redirects_the_name(session):
    e = _entry(session)
    a = areas.resolve_area(session, "Digestive", "area")
    b = areas.resolve_area(session, "Digestive system", "area")
    areas.set_tags(session, e, ["Digestive", "Digestive system"], [])
    areas.merge_areas(session, a.id, b.id)
    assert [x.id for x in areas.tags_of(session, e.id)] == [b.id]
    assert areas.resolve_area(session, "Digestive", "area").id == b.id              # the old name now leads to the kept one


def test_approve_and_rename(session):
    a = areas.resolve_area(session, "Skin", "area")
    areas.approve_area(session, a.id)
    areas.rename_area(session, a.id, "Skin and hair")
    session.refresh(a)
    assert a.approved and a.name == "Skin and hair" and a.match_key == "skin and hair"
    assert areas.names_for_prompt(session, "area") == ["Skin and hair"]


def test_renaming_onto_an_existing_name_is_refused_with_a_three_part_message(session):
    a = areas.resolve_area(session, "Skin", "area")
    areas.resolve_area(session, "Eyes", "area")
    with pytest.raises(areas.AreaError) as exc:
        areas.rename_area(session, a.id, "eyes")
    p = exc.value.problem
    assert p.what and p.why and p.next_step
