from app.models.person import Person


def test_create_and_read_person(session):
    person = Person(
        name="Eduardo Manuel da Silva",
        notes="Informal loan, SEPA transfer 2024-05",
    )
    session.add(person)
    session.commit()
    session.refresh(person)

    fetched = session.get(Person, person.id)
    assert fetched.name == "Eduardo Manuel da Silva"
    assert fetched.notes == "Informal loan, SEPA transfer 2024-05"


def test_person_notes_optional(session):
    person = Person(name="Anonymous Lender")
    session.add(person)
    session.commit()
    session.refresh(person)

    assert person.notes is None
