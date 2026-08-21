from decimal import Decimal

from app.models.commitment import Cadence, Commitment
from app.models.debt import Debt, DebtDirection, DebtKind
from app.models.person import Person
from app.models.transaction import Category


def test_informal_debt_links_to_person(session):
    person = Person(name="Eduardo Manuel da Silva")
    session.add(person)
    session.commit()
    session.refresh(person)

    debt = Debt(
        kind=DebtKind.INFORMAL,
        person_id=person.id,
        direction=DebtDirection.OWED_TO_US,
        original_amount=5000.0,
        current_balance=5000.0,
    )
    session.add(debt)
    session.commit()
    session.refresh(debt)

    fetched = session.get(Debt, debt.id)
    assert fetched.kind == DebtKind.INFORMAL
    assert fetched.person_id == person.id
    assert fetched.direction == DebtDirection.OWED_TO_US
    assert fetched.commitment_id is None


def test_formal_debt_links_to_commitment(session):
    commitment = Commitment(
        name="Mortgage payment",
        category=Category.HOME,
        cadence=Cadence.MONTHLY,
        planned_amount=650.0,
    )
    session.add(commitment)
    session.commit()
    session.refresh(commitment)

    debt = Debt(
        kind=DebtKind.FORMAL,
        original_amount=142300.0,
        current_balance=142300.0,
        interest_rate=0.035,
        commitment_id=commitment.id,
    )
    session.add(debt)
    session.commit()
    session.refresh(debt)

    fetched = session.get(Debt, debt.id)
    assert fetched.kind == DebtKind.FORMAL
    assert fetched.person_id is None
    assert fetched.direction is None
    assert fetched.commitment_id == commitment.id


def test_current_balance_preserves_decimal_precision(session):
    debt = Debt(
        kind=DebtKind.FORMAL,
        original_amount=1000.0,
        current_balance=Decimal("1000.00"),
    )
    session.add(debt)
    session.commit()
    session.refresh(debt)

    fetched = session.get(Debt, debt.id)
    assert isinstance(fetched.current_balance, Decimal)
    assert fetched.current_balance == Decimal("1000.00")

    # Three payments that don't divide evenly demonstrate exactly why this
    # column can't be a float: repeated float subtraction of a
    # non-terminating binary fraction like 333.33 leaves a residual instead
    # of landing on exactly zero.
    remaining = (
        fetched.current_balance - Decimal("333.33") - Decimal("333.33") - Decimal("333.34")
    )
    assert remaining == Decimal("0.00")
