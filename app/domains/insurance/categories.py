"""Insurance paper kinds (stored in Document.category) and the details every insurance paper carries.

The kind is what the paper is (work-list B1b, Pedro 2026-11-04): a policy paper or a claim. What the policy covers
(home, car, health, life) is a detail, `covers`; which part of the policy a paper is, `part`. A premium notice is
first of all a bill: it is filed once, under Bills, and shown on its policy's card too (policies.py), so it is never
counted twice."""

from enum import Enum


class InsuranceKind(str, Enum):
    POLICY = "policy"
    CLAIM = "claim"


ALL_KINDS = frozenset(k.value for k in InsuranceKind)

# Offered on the Insurance upload form next to the two kinds; filed as a Money bill (see routers/insurance.py).
PREMIUM_NOTICE = "premium_notice"

# What a policy covers: landing-page order and section titles.
COVERS = (
    ("home", "Home"),
    ("car", "Cars"),
    ("health", "Health"),
    ("life", "Life"),
)
SECTION_TITLES = dict(COVERS)

# Which part of a policy a policy paper is, in the order a policy card lists them.
POLICY_PARTS = (
    ("policy_schedule", "Policy schedule (particular conditions)"),
    ("policy_summary", "Policy summary"),
    ("general_conditions", "General conditions"),
    ("payment_plan", "Payment plan"),
    ("green_card", "Green card (car)"),
    ("other", "Other"),
)
PART_ORDER = {value: i for i, (value, _label) in enumerate(POLICY_PARTS)}


def insurance_derived_fields(kind, fields: dict[str, str]) -> dict[str, str]:
    """`covers_label`: "Home insurance" for the policy's wiki page (never stored)."""
    title = SECTION_TITLES.get(fields.get("covers", ""))
    return {"covers_label": f"{title} insurance"} if title else {}


POLICY_PAGE_TYPE = "insurance.policy"
