"""Health document categories (stored in Document.paper_kind): what kind of paper it is."""

from enum import Enum


class HealthKind(str, Enum):
    LAB_RESULT = "lab_result"
    IMAGING_REPORT = "imaging_report"
    MEDICAL_REPORT = "medical_report"
    PRESCRIPTION = "prescription"
    REFERRAL = "referral"
    # LEGACY: bills are not Health papers any more (they belong under Bills / Insurance). The two members stay only so
    # that a paper already filed under them still opens; they are in LEGACY_KINDS, not in ALL_KINDS.
    MEDICAL_BILL = "medical_bill"
    CLAIM_STATEMENT = "claim_statement"
    VACCINATION = "vaccination"
    APPOINTMENT = "appointment"


LEGACY_KINDS = frozenset({HealthKind.MEDICAL_BILL.value, HealthKind.CLAIM_STATEMENT.value})
LEGACY_LABELS = {HealthKind.MEDICAL_BILL.value: "Medical bill (no longer a Health kind)",
                 HealthKind.CLAIM_STATEMENT.value: "Reimbursement statement (no longer a Health kind)"}

# What the reader may return and what validates a category: every current kind, never a legacy one.
ALL_KINDS = frozenset(c.value for c in HealthKind) - LEGACY_KINDS

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
