"""Health's CategorySpec. Always house_high: declared here (forced_tier), read by app/privacy/settings.py."""

from app.categories.base import (
    KindSpec, CategorySpec, EntityTypeSpec, FactPolicy, FactSpec, FieldKind, FieldSpec, NavLink, WikiSchema,
)
from app.categories.health.ask import HEALTH_TOOLS
from app.categories.health.kinds import ALL_KINDS, PERSON_PAGE_TYPE, RECORD_KINDS, HealthKind
from app.categories.health.handler import HealthHandler
from app.categories.health.overview import health_overview_card
from app.models.category import Category
from app.privacy.tiers import Tier

_GUIDANCE = (
    "Health records of the family. Record standing facts per person that are worth finding later: long-term "
    "conditions and diagnoses, allergies, ongoing medication, vaccination status, the date and outcome of the "
    "last check-up or exam of each kind, and the provider usually seen for each area. Never record one-off "
    "amounts or appointment logistics. Title a page after the person and the topic, e.g. 'Maria: vaccinations'."
)

SPEC = CategorySpec(
    category=Category.HEALTH,
    forced_tier=Tier.HIGH,          # nothing about Health may ever be read below High (decision D19)
    label="Health",
    description=(
        "The family's medical papers: lab results, imaging reports, consultation and discharge letters, "
        "prescriptions, referrals, vaccination records and appointment letters."
    ),
    home_url="/health/records",
    kinds=(
        KindSpec(HealthKind.LAB_RESULT.value, "Lab result", "Blood, urine or other laboratory results."),
        KindSpec(HealthKind.IMAGING_REPORT.value, "Imaging report",
                     "An X-ray, ultrasound, MRI, CT or similar report."),
        KindSpec(HealthKind.MEDICAL_REPORT.value, "Medical report",
                     "A consultation note, discharge letter, medical certificate or other clinical letter."),
        KindSpec(HealthKind.PRESCRIPTION.value, "Prescription", "A prescription for medicines or treatment."),
        KindSpec(HealthKind.REFERRAL.value, "Referral", "A referral to a specialist, exam or treatment."),
        KindSpec(HealthKind.VACCINATION.value, "Vaccination record",
                     "A vaccination card, certificate or record."),
        KindSpec(HealthKind.APPOINTMENT.value, "Appointment",
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
                  kinds=frozenset({HealthKind.APPOINTMENT.value})),
        FieldSpec("appointment_time", "Appointment time (HH:MM)", FieldKind.TEXT,
                  kinds=frozenset({HealthKind.APPOINTMENT.value})),
        FieldSpec("indication", "Why this was done", FieldKind.LONGTEXT, private=True,
                  help="The reason the paper was requested, as printed (Indicação, Motivo). If the paper gives none, write it here."),
        FieldSpec("clinical_notes", "Clinical notes on the request", FieldKind.LONGTEXT, private=True,
                  help="What the requester wrote about the patient (Observações, Informação clínica)."),
        FieldSpec("requested_by", "Requested by / referred from", FieldKind.TEXT, private=True),
        FieldSpec("performed_by", "Done by", FieldKind.TEXT, private=True),
        FieldSpec("summary", "Summary", FieldKind.LONGTEXT, private=True),
        FieldSpec("details", "Other details printed on the paper", FieldKind.LONGTEXT, private=True),
        FieldSpec("notes", "Notes", FieldKind.LONGTEXT, private=True),
    ),
    handler=HealthHandler(),
    infers_kind=True,       # the upload page asks only for the file; the reading proposes the kind of paper
    ask_tools=HEALTH_TOOLS,
    nav_links=(NavLink("Records", "/health/records"), NavLink("Areas", "/health/areas"), NavLink("Topics", "/health/topics"),
               NavLink("People", "/health/people")),
    overview_card=health_overview_card,
    document_url=lambda document: f"/health/documents/{document.id}",
    wiki=WikiSchema(
        guidance=_GUIDANCE,
        entity_types=(
            EntityTypeSpec(
                page_type=PERSON_PAGE_TYPE, label="People", key_field="person", kinds=ALL_KINDS,
                facts=(FactSpec("last_record", "Most recent paper", "document_date", policy=FactPolicy.LATEST),),
            ),
        ),
    ),
)
