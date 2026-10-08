"""Insurance document categories (stored in Document.category): what the policy covers."""

from enum import Enum


class InsuranceCategory(str, Enum):
    HOME = "home_insurance"
    CAR = "car_insurance"
    HEALTH = "health_insurance"
    LIFE = "life_insurance"


# Landing-page order and section titles.
SECTION_TITLES = {
    InsuranceCategory.HOME.value: "Home",
    InsuranceCategory.CAR.value: "Cars",
    InsuranceCategory.HEALTH.value: "Health",
    InsuranceCategory.LIFE.value: "Life",
}
ALL_CATEGORIES = frozenset(SECTION_TITLES)

# What a document of a policy is, in the order a policy card lists them.
DOCUMENT_KINDS = (
    ("policy_schedule", "Policy schedule (particular conditions)"),
    ("policy_summary", "Policy summary"),
    ("general_conditions", "General conditions"),
    ("premium_notice", "Premium notice / receipt"),
    ("payment_plan", "Payment plan"),
    ("green_card", "Green card (car)"),
    ("claim", "Claim"),
    ("other", "Other"),
)
KIND_ORDER = {value: i for i, (value, _label) in enumerate(DOCUMENT_KINDS)}

POLICY_PAGE_TYPE = "insurance.policy"
