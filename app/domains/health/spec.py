"""Health's DomainSpec. Always house_high (app/privacy/settings.py FORCED_TIERS)."""

from app.domains.base import (
    CategorySpec, DomainSpec, EntityTypeSpec, FactPolicy, FactSpec, FieldKind, FieldSpec, NavLink, WikiSchema,
)
from app.domains.health.ask import HEALTH_TOOLS
from app.domains.health.categories import ALL_CATEGORIES, PERSON_PAGE_TYPE, RECORD_KINDS, HealthCategory
from app.domains.health.handler import HealthHandler
from app.domains.health.overview import health_overview_card
from app.models.domain import Domain

_GUIDANCE = (
    "Health records of the family. Record standing facts per person that are worth finding later: long-term "
    "conditions and diagnoses, allergies, ongoing medication, vaccination status, the date and outcome of the "
    "last check-up or exam of each kind, and the provider usually seen for each area. Never record one-off "
    "amounts or appointment logistics. Title a page after the person and the topic, e.g. 'Maria: vaccinations'."
)

SPEC = DomainSpec(
    domain=Domain.HEALTH,
    label="Health",
    description=(
        "The family's medical papers: lab results, imaging reports, consultation and discharge letters, "
        "prescriptions, referrals, medical bills, health-insurance reimbursement statements, vaccination "
        "records and appointment letters."
    ),
    home_url="/health/records",
    categories=(
        CategorySpec(HealthCategory.LAB_RESULT.value, "Lab result", "Blood, urine or other laboratory results."),
        CategorySpec(HealthCategory.IMAGING_REPORT.value, "Imaging report",
                     "An X-ray, ultrasound, MRI, CT or similar report."),
        CategorySpec(HealthCategory.MEDICAL_REPORT.value, "Medical report",
                     "A consultation note, discharge letter, medical certificate or other clinical letter."),
        CategorySpec(HealthCategory.PRESCRIPTION.value, "Prescription", "A prescription for medicines or treatment."),
        CategorySpec(HealthCategory.REFERRAL.value, "Referral", "A referral to a specialist, exam or treatment."),
        CategorySpec(HealthCategory.MEDICAL_BILL.value, "Medical bill",
                     "An invoice or receipt from a doctor, clinic, hospital, dentist or pharmacy."),
        CategorySpec(HealthCategory.CLAIM_STATEMENT.value, "Reimbursement statement",
                     "A health insurer's statement of what it reimbursed or refused for medical expenses."),
        CategorySpec(HealthCategory.VACCINATION.value, "Vaccination record",
                     "A vaccination card, certificate or record."),
        CategorySpec(HealthCategory.APPOINTMENT.value, "Appointment",
                     "A letter, email or message confirming a medical appointment."),
    ),
    fields=(
        FieldSpec("person", "Whose it is", FieldKind.SUGGEST, help="The person the paper is about."),
        FieldSpec("document_date", "Date of the paper", FieldKind.DATE),
        FieldSpec("provider", "Doctor / clinic / lab", FieldKind.SUGGEST),
        FieldSpec("provider_phone", "Provider phone", FieldKind.TEXT),
        FieldSpec("provider_address", "Provider address", FieldKind.TEXT),
        FieldSpec("record_kind", "What kind of event", FieldKind.CHOICE, choices=RECORD_KINDS, private=True),
        FieldSpec("appointment_date", "Appointment date", FieldKind.DATE,
                  categories=frozenset({HealthCategory.APPOINTMENT.value})),
        FieldSpec("appointment_time", "Appointment time (HH:MM)", FieldKind.TEXT,
                  categories=frozenset({HealthCategory.APPOINTMENT.value})),
        FieldSpec("amount_billed", "Amount billed (EUR)", FieldKind.TEXT),
        FieldSpec("amount_reimbursed", "Amount reimbursed (EUR)", FieldKind.TEXT),
        FieldSpec("amount_owed", "Amount still owed (EUR)", FieldKind.TEXT),
        FieldSpec("summary", "Summary", FieldKind.LONGTEXT, private=True),
        FieldSpec("notes", "Notes", FieldKind.LONGTEXT, private=True),
    ),
    handler=HealthHandler(),
    ask_tools=HEALTH_TOOLS,
    nav_links=(NavLink("Records", "/health/records"), NavLink("Areas", "/health/areas"), NavLink("People", "/health/people")),
    overview_card=health_overview_card,
    document_url=lambda document: f"/health/documents/{document.id}",
    wiki=WikiSchema(
        guidance=_GUIDANCE,
        entity_types=(
            EntityTypeSpec(
                page_type=PERSON_PAGE_TYPE, label="People", key_field="person", categories=ALL_CATEGORIES,
                facts=(FactSpec("last_record", "Most recent paper", "document_date", policy=FactPolicy.LATEST),),
            ),
        ),
    ),
)
