"""Health document categories (stored in Document.category): what kind of paper it is."""

from enum import Enum


class HealthCategory(str, Enum):
    LAB_RESULT = "lab_result"
    IMAGING_REPORT = "imaging_report"
    MEDICAL_REPORT = "medical_report"
    PRESCRIPTION = "prescription"
    REFERRAL = "referral"
    MEDICAL_BILL = "medical_bill"
    CLAIM_STATEMENT = "claim_statement"
    VACCINATION = "vaccination"
    APPOINTMENT = "appointment"


ALL_CATEGORIES = frozenset(c.value for c in HealthCategory)

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
