"""Cross-cutting tags (see tag_service.py): which tree nodes carry which tag.

A slug may be a leaf, a category or a whole group; a tag on a node covers everything below it.
A node can carry several tags (house-loan life insurance is `insurance`, `loan` and `house`).
Edit here and ship a migration that calls ensure_tags() (see e9c1a7d3b542): it brings the database in line and removes dropped links.
Tags measure spending only: inflow leaves under a tagged group are ignored."""

TAG_LABELS = {
    "insurance": "Insurance",
    "tax": "Tax",
    "loan": "Loan",
    "house": "House",
    "car": "Car",
    "health": "Health",
    "subscription": "Subscriptions",
}

TAG_ASSIGNMENTS = {
    "insurance": ["insurances"],
    "tax": ["taxes-financial-costs.taxes"],
    "loan": [
        "loans-debt",  # repayments, interest & fees, money lent out (the inflow leaves do not count)
        "insurances.home.life-insurance-house-loan",
        "insurances.home.building-insurance-house-loan",
    ],
    "house": [
        "housing",
        "insurances.home",
        "taxes-financial-costs.taxes.imi-property-tax",
        "loans-debt.loan-repayments.mortgage",
        "loans-debt.interest-fees",
    ],
    "car": [
        "transport.car-running-costs",
        "insurances.cars",
        "taxes-financial-costs.taxes.iuc-road-tax",
        "loans-debt.loan-repayments.car-loan",
    ],
    "health": ["health", "insurances.personal.health-insurance"],
    "subscription": ["housing.utilities.streaming", "housing.utilities.software-apps"],
}
