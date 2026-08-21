from datetime import date

from app.models.commitment import Cadence, Commitment
from app.models.transaction import Category


def test_create_yearly_commitment(session):
    commitment = Commitment(
        name="IMI 2026",
        category=Category.HOME,
        cadence=Cadence.YEARLY,
        planned_amount=4800.0,
        year=2026,
        next_due_date=date(2026, 11, 30),
    )
    session.add(commitment)
    session.commit()
    session.refresh(commitment)

    fetched = session.get(Commitment, commitment.id)
    assert fetched.cadence == Cadence.YEARLY
    assert fetched.year == 2026
    assert fetched.planned_amount == 4800.0
    assert fetched.next_due_date == date(2026, 11, 30)


def test_yearly_commitment_two_years_are_distinct_rows(session):
    commitment_2026 = Commitment(
        name="IMI",
        category=Category.HOME,
        cadence=Cadence.YEARLY,
        planned_amount=4800.0,
        year=2026,
    )
    commitment_2027 = Commitment(
        name="IMI",
        category=Category.HOME,
        cadence=Cadence.YEARLY,
        planned_amount=5200.0,
        year=2027,
    )
    session.add(commitment_2026)
    session.add(commitment_2027)
    session.commit()
    session.refresh(commitment_2026)
    session.refresh(commitment_2027)

    assert commitment_2026.id != commitment_2027.id

    fetched_2026 = session.get(Commitment, commitment_2026.id)
    fetched_2027 = session.get(Commitment, commitment_2027.id)

    assert fetched_2026.name == "IMI"
    assert fetched_2026.year == 2026
    assert fetched_2026.planned_amount == 4800.0

    assert fetched_2027.name == "IMI"
    assert fetched_2027.year == 2027
    assert fetched_2027.planned_amount == 5200.0


def test_create_monthly_commitment_leaves_year_none(session):
    commitment = Commitment(
        name="Netflix",
        category=Category.SUBSCRIPTIONS,
        cadence=Cadence.MONTHLY,
        planned_amount=15.99,
    )
    session.add(commitment)
    session.commit()
    session.refresh(commitment)

    assert commitment.cadence == Cadence.MONTHLY
    assert commitment.year is None
    assert commitment.next_due_date is None
