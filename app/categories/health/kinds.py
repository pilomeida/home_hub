"""Health document categories (stored in Document.paper_kind): what kind of paper it is."""

from enum import Enum


class HealthKind(str, Enum):
    LAB_RESULT = "lab_result"
    IMAGING_REPORT = "imaging_report"
    MEDICAL_REPORT = "medical_report"
    PRESCRIPTION = "prescription"
    REFERRAL = "referral"
    VACCINATION = "vaccination"
    APPOINTMENT = "appointment"


# What the reader may return and what validates a category. (The two bill kinds, medical_bill and claim_statement,
# were retired 2026-11-20, PLAN step 9 D1: bills are Money papers and no live paper was filed under them.)
ALL_KINDS = frozenset(c.value for c in HealthKind)

# What kind of event the paper belongs to (the "check-up" facet of the Health tab).
RECORD_KINDS = (
    ("check_up", "Check-up"),
    ("follow_up", "Follow-up"),
    ("test", "Test / exam"),
    ("procedure", "Procedure / surgery"),
    ("emergency", "Emergency"),
    ("dental", "Dental"),
    ("vaccination", "Vaccination"),
    ("other", "Other"),
)
KIND_LABELS = dict(RECORD_KINDS)

PERSON_PAGE_TYPE = "health.person"
