"""Vehicles' CategorySpec (PLAN step 8c)."""

from app.categories.base import CategorySpec, FieldKind, FieldSpec, KindSpec, NavLink, dated_by_fields
from app.categories.vehicles.garage import INSPECTION, ROAD_TAX, SERVICE
from app.categories.vehicles.handler import VehiclesHandler
from app.categories.vehicles.kinds import VehicleKind
from app.categories.vehicles.overview import vehicles_overview_card
from app.models.category import Category
from app.privacy.tiers import Tier

_K = VehicleKind

SPEC = CategorySpec(
    category=Category.VEHICLES,
    label="Vehicles",
    description=(
        "The household's cars: registration papers (Documento Único, livrete), inspection certificates (IPO), road tax "
        "(IUC) notices and proofs of payment, and workshop service invoices. Car insurance is under Insurance."
    ),
    home_url="/vehicles",
    from_guess={"paper_date": "paper_date"},
    kinds=(
        KindSpec(_K.REGISTRATION.value, "Registration", "A car's registration: Documento Único Automóvel or livrete.",
                 suggested_tier=Tier.MID),
        KindSpec(_K.INSPECTION.value, "Inspection (IPO)", "A car's periodic inspection certificate (IPO).",
                 suggested_tier=Tier.MID, reminder=INSPECTION),
        KindSpec(_K.ROAD_TAX.value, "Road tax (IUC)", "A car's road tax (IUC) notice or proof of payment.",
                 suggested_tier=Tier.MID, reminder=ROAD_TAX),
        KindSpec(_K.SERVICE.value, "Service", "A workshop's invoice or record of a car's service or repair.",
                 suggested_tier=Tier.MID, reminder=SERVICE),
    ),
    fields=(
        FieldSpec("plate", "Plate", FieldKind.SUGGEST, help="e.g. 82-CL-65. Read from the paper when left empty."),
        FieldSpec("vehicle", "Car", FieldKind.SUGGEST, help="Make and model, e.g. Toyota Avensis."),
        FieldSpec("paper_date", "Date of the paper", FieldKind.DATE),
        FieldSpec("next_inspection_on", "Next inspection", FieldKind.DATE,
                  kinds=frozenset({_K.INSPECTION.value}), help="A To-Do comes 30 days before."),
        FieldSpec("road_tax_due_on", "Next road tax payment", FieldKind.DATE,
                  kinds=frozenset({_K.ROAD_TAX.value}), help="A To-Do comes 14 days before."),
        FieldSpec("next_service_on", "Next service", FieldKind.DATE,
                  kinds=frozenset({_K.SERVICE.value}), help="A To-Do comes 21 days before."),
        FieldSpec("mileage", "Kilometres", FieldKind.TEXT, kinds=frozenset({_K.INSPECTION.value, _K.SERVICE.value})),
        FieldSpec("notes", "Notes", FieldKind.LONGTEXT),
    ),
    handler=VehiclesHandler(),
    nav_links=(NavLink("Vehicles", "/vehicles"),),
    overview_card=vehicles_overview_card,
    document_url=lambda document: f"/vehicles/documents/{document.id}",
    dated_by=dated_by_fields("paper_date"),
)
