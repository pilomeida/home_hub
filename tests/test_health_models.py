from sqlmodel import SQLModel

def test_the_five_tables_exist():
    from app import models  # noqa: F401
    names = set(SQLModel.metadata.tables)
    assert {"health_people", "health_areas", "health_entries", "health_entry_tags", "health_measurements"} <= names


def test_topics_outline_profiles_and_the_new_columns_round_trip(session):
    from app.models.domain import Domain
    from app.models.health import HealthEntry, HealthMeasurement, HealthOutlineProfile, HealthTopic
    from tests.knowledge_factories import make_document

    root = HealthTopic(name="Urina", match_key="urina")
    session.add(root); session.commit(); session.refresh(root)
    child = HealthTopic(name="Caracteres gerais", match_key="caracteres gerais", parent_id=root.id)
    session.add(child); session.commit(); session.refresh(child)
    assert root.parent_id is None and child.parent_id == root.id
    assert root.approved is False and root.merged_into_id is None and root.similar_to_id is None

    doc = make_document(session, domain=Domain.HEALTH, category="lab_result")
    entry = HealthEntry(document_id=doc.id, layout="report", outline_confirmed_by="Pedro")
    session.add(entry); session.commit(); session.refresh(entry)
    assert entry.layout == "report" and entry.outline_confirmed_by == "Pedro"
    assert entry.outline_confirmed_at is None
    m = HealthMeasurement(entry_id=entry.id, test="pH", topic_id=child.id)
    session.add(m); session.commit(); session.refresh(m)
    assert m.topic_id == child.id
    assert HealthEntry(document_id=0).layout is None

    prof = HealthOutlineProfile(provider_key="lab x", provider="Lab X", layout="report",
                                outline_json='["Urina", "Urina > Caracteres gerais"]')
    session.add(prof); session.commit(); session.refresh(prof)
    assert prof.outline_json.startswith("[") and prof.confirmed_by is None and prof.confirmed_at is not None
