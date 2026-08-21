import pytest
from sqlalchemy.exc import IntegrityError

from app.models.merchant import Merchant
from app.models.transaction import Category, Nature


def test_create_and_read_merchant(session):
    merchant = Merchant(
        canonical_name="Modelo Hiper",
        default_category=Category.GROCERIES,
        default_nature=Nature.ESSENTIAL,
        normalized_key="modelo hiper",
    )
    session.add(merchant)
    session.commit()
    session.refresh(merchant)

    fetched = session.get(Merchant, merchant.id)
    assert fetched.canonical_name == "Modelo Hiper"
    assert fetched.default_category == Category.GROCERIES
    assert fetched.default_nature == Nature.ESSENTIAL
    assert fetched.normalized_key == "modelo hiper"
    assert fetched.confirmed is False
    assert fetched.recurring_reviewed is False


def test_normalized_key_is_unique(session):
    session.add(Merchant(
        canonical_name="Modelo Hiper", default_category=Category.GROCERIES,
        normalized_key="modelo hiper",
    ))
    session.commit()

    session.add(Merchant(
        canonical_name="Modelo Hiper Duplicate", default_category=Category.GROCERIES,
        normalized_key="modelo hiper",
    ))
    with pytest.raises(IntegrityError):
        session.commit()
