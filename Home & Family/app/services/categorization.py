"""Normalizes free-text category hints from extraction into the controlled
Category vocabulary."""

from app.models.transaction import Category

_SYNONYMS: dict[str, Category] = {
    "electricity": Category.ELECTRICITY,
    "power": Category.ELECTRICITY,
    "energy": Category.ELECTRICITY,
    "water": Category.WATER,
    "gas": Category.GAS,
    "telecom": Category.TELECOM,
    "telco": Category.TELECOM,
    "internet": Category.TELECOM,
    "phone": Category.TELECOM,
    "mobile": Category.TELECOM,
    "insurance": Category.INSURANCE,
    "subscriptions": Category.SUBSCRIPTIONS,
    "subscription": Category.SUBSCRIPTIONS,
    "streaming": Category.SUBSCRIPTIONS,
    "groceries": Category.GROCERIES,
    "grocery": Category.GROCERIES,
    "supermarket": Category.GROCERIES,
    "health": Category.HEALTH,
    "medical": Category.HEALTH,
    "pharmacy": Category.HEALTH,
    "home": Category.HOME,
    "maintenance": Category.HOME,
    "income": Category.INCOME,
    "salary": Category.INCOME,
    "transfer": Category.TRANSFER,
    "atm_withdrawal": Category.ATM_WITHDRAWAL,
    "atm": Category.ATM_WITHDRAWAL,
    "withdrawal": Category.ATM_WITHDRAWAL,
    "restaurants": Category.RESTAURANTS,
    "restaurant": Category.RESTAURANTS,
    "dining": Category.RESTAURANTS,
    "shopping": Category.SHOPPING,
    "retail": Category.SHOPPING,
    "other_expense": Category.OTHER_EXPENSE,
}


def normalize_category(hint: str) -> Category:
    """Map a free-text category hint to the closest controlled Category,
    falling back to OTHER when nothing matches."""
    if not hint:
        return Category.OTHER
    return _SYNONYMS.get(hint.strip().lower(), Category.OTHER)
