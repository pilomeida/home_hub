"""Insurance's DomainSpec."""

from app.domains.base import (
    CategorySpec, DomainSpec, EntityTypeSpec, FactSpec, FieldKind, FieldSpec, NavLink, WikiSchema,
)
from app.domains.insurance.categories import (
    ALL_KINDS, COVERS, POLICY_PAGE_TYPE, POLICY_PARTS, InsuranceKind, insurance_derived_fields,
)
from app.domains.insurance.handler import InsuranceHandler
from app.domains.insurance.overview import insurance_overview_card
from app.models.domain import Domain

SPEC = DomainSpec(
    domain=Domain.INSURANCE,
    label="Insurance",
    description=(
        "The household's insurance policies, for the home, the cars, health and life: the policy's own papers "
        "(conditions booklets, schedules, summaries, payment plans, green cards) and claims. A premium notice asks "
        "for money: it is a bill, filed under Bills and shown on its policy."
    ),
    home_url="/insurance",
    categories=(
        CategorySpec(InsuranceKind.POLICY.value, "Policy",
                     "A policy's own paper: general or particular conditions, schedule, summary, payment plan or "
                     "green card, for the house, a car, health or life. A premium notice or receipt is NOT this: "
                     "it is a bill, dealt with under Bills. A statement of what a health insurer reimbursed is "
                     "a bill-type paper too."),
        CategorySpec(InsuranceKind.CLAIM.value, "Claim",
                     "A claim made on a policy, or the insurer's answer to one."),
    ),
    fields=(
        FieldSpec("insurer", "Insurer", FieldKind.SUGGEST, required=True),
        FieldSpec("policy_number", "Policy number", FieldKind.TEXT, required=True),
        FieldSpec("covers", "What the policy is for", FieldKind.CHOICE, required=True, choices=COVERS),
        FieldSpec("part", "Which part of the policy", FieldKind.CHOICE,
                  categories=frozenset({InsuranceKind.POLICY.value}), required=True, choices=POLICY_PARTS),
        FieldSpec("insured", "What or whom it covers", FieldKind.SUGGEST,
                  help="e.g. Mafra house, Toyota Avensis 82-CL-65, the family."),
        FieldSpec("product", "Product", FieldKind.TEXT, help="e.g. Zurich Lar Seguro."),
        FieldSpec("broker", "Broker / distributor", FieldKind.SUGGEST),
        FieldSpec("broker_contact", "Broker contact", FieldKind.TEXT),
        FieldSpec("edition", "Edition / validity of the document", FieldKind.TEXT),
        FieldSpec("notes", "Notes", FieldKind.LONGTEXT,
                  help="Premium, what it covers, which loan it belongs to, anything to remember."),
    ),
    derive_fields=insurance_derived_fields,
    handler=InsuranceHandler(),
    nav_links=(NavLink("Policies", "/insurance"),),
    overview_card=insurance_overview_card,
    document_url=lambda document: f"/insurance/documents/{document.id}",
    wiki=WikiSchema(
        entity_types=(
            EntityTypeSpec(
                page_type=POLICY_PAGE_TYPE, label="Policies", key_field="policy_number",
                categories=ALL_KINDS,
                facts=(
                    FactSpec("type", "Type", "covers_label"),
                    FactSpec("insurer", "Insurer", "insurer"),
                    FactSpec("product", "Product", "product"),
                    FactSpec("covers", "Covers", "insured"),
                ),
            ),
        ),
    ),
)
