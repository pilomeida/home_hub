"""Insurance's DomainSpec."""

from app.domains.base import (
    CategorySpec, DomainSpec, EntityTypeSpec, FactSpec, FieldKind, FieldSpec, NavLink, WikiSchema,
)
from app.domains.insurance.categories import ALL_CATEGORIES, DOCUMENT_KINDS, POLICY_PAGE_TYPE, InsuranceCategory
from app.domains.insurance.handler import InsuranceHandler
from app.domains.insurance.overview import insurance_overview_card
from app.models.domain import Domain

SPEC = DomainSpec(
    domain=Domain.INSURANCE,
    label="Insurance",
    description=(
        "The household's insurance policies: conditions booklets, policy schedules and summaries, premium "
        "notices, payment plans and green cards, for the home, the cars, health and life."
    ),
    home_url="/insurance",
    categories=(
        CategorySpec(InsuranceCategory.HOME.value, "Home insurance",
                     "A policy for the house or its contents: conditions, schedule, summary, premium notice or payment plan."),
        CategorySpec(InsuranceCategory.CAR.value, "Car insurance",
                     "A car policy: conditions, schedule, premium notice, payment plan or green card."),
        CategorySpec(InsuranceCategory.HEALTH.value, "Health insurance",
                     "A health insurance policy for one or more people: conditions, schedule or premium notice."),
        CategorySpec(InsuranceCategory.LIFE.value, "Life insurance",
                     "A life insurance policy: conditions, schedule or premium notice."),
    ),
    fields=(
        FieldSpec("insurer", "Insurer", FieldKind.SUGGEST, required=True),
        FieldSpec("policy_number", "Policy number", FieldKind.TEXT, required=True),
        FieldSpec("document_kind", "What this document is", FieldKind.CHOICE, required=True, choices=DOCUMENT_KINDS),
        FieldSpec("insured", "What or whom it covers", FieldKind.SUGGEST,
                  help="e.g. Mafra house, Toyota Avensis 82-CL-65, the family."),
        FieldSpec("product", "Product", FieldKind.TEXT, help="e.g. Zurich Lar Seguro."),
        FieldSpec("broker", "Broker / distributor", FieldKind.SUGGEST),
        FieldSpec("broker_contact", "Broker contact", FieldKind.TEXT),
        FieldSpec("edition", "Edition / validity of the document", FieldKind.TEXT),
        FieldSpec("notes", "Notes", FieldKind.LONGTEXT,
                  help="Premium, what it covers, which loan it belongs to, anything to remember."),
    ),
    handler=InsuranceHandler(),
    nav_links=(NavLink("Policies", "/insurance"),),
    overview_card=insurance_overview_card,
    document_url=lambda document: f"/insurance/documents/{document.id}",
    wiki=WikiSchema(
        entity_types=(
            EntityTypeSpec(
                page_type=POLICY_PAGE_TYPE, label="Policies", key_field="policy_number",
                categories=ALL_CATEGORIES,
                facts=(
                    FactSpec("type", "Type", "category"),
                    FactSpec("insurer", "Insurer", "insurer"),
                    FactSpec("product", "Product", "product"),
                    FactSpec("covers", "Covers", "insured"),
                ),
            ),
        ),
    ),
)
