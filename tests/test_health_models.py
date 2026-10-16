from sqlmodel import SQLModel

def test_the_five_tables_exist():
    from app import models  # noqa: F401
    names = set(SQLModel.metadata.tables)
    assert {"health_people", "health_areas", "health_entries", "health_entry_tags", "health_measurements"} <= names
