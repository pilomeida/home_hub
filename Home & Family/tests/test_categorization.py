import pytest

from app.models.transaction import Category
from app.services.categorization import normalize_category


@pytest.mark.parametrize("hint,expected", [
    ("electricity", Category.ELECTRICITY),
    ("Power", Category.ELECTRICITY),
    ("water", Category.WATER),
    ("internet", Category.TELECOM),
    ("streaming", Category.SUBSCRIPTIONS),
    ("supermarket", Category.GROCERIES),
    ("pharmacy", Category.HEALTH),
    ("maintenance", Category.HOME),
    ("something-unrecognized", Category.OTHER),
    ("", Category.OTHER),
])
def test_normalize_category(hint, expected):
    assert normalize_category(hint) == expected


@pytest.mark.parametrize("hint,expected", [
    ("income", Category.INCOME),
    ("salary", Category.INCOME),
    ("transfer", Category.TRANSFER),
    ("atm_withdrawal", Category.ATM_WITHDRAWAL),
    ("atm", Category.ATM_WITHDRAWAL),
    ("withdrawal", Category.ATM_WITHDRAWAL),
    ("restaurants", Category.RESTAURANTS),
    ("restaurant", Category.RESTAURANTS),
    ("dining", Category.RESTAURANTS),
    ("shopping", Category.SHOPPING),
    ("retail", Category.SHOPPING),
    ("other_expense", Category.OTHER_EXPENSE),
])
def test_normalize_category_new_statement_categories(hint, expected):
    assert normalize_category(hint) == expected
