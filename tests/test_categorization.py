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
