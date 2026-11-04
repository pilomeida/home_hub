"""House's DomainSpec."""

from app.domains.base import (
    DEFAULT_MEDIA, KindSpec, DomainSpec, EntityTypeSpec, FactPolicy, FactSpec, FieldKind, FieldSpec,
    LinkSpec, MediaKind, NavLink, SourceKind, WikiSchema,
)
from app.domains.house.categories import ITEM_CATEGORIES, ITEM_KIND_CATEGORIES, ITEM_PAGE_TYPE, ROOM_PAGE_TYPE, HouseCategory
from app.domains.house.handler import HouseHandler
from app.domains.house.overview import house_overview_card
from app.domains.house.warranty import house_derived_fields
from app.models.domain import Domain

_WARRANTY = frozenset({HouseCategory.WARRANTY_INVOICE.value})
_MAINTENANCE = frozenset({HouseCategory.MAINTENANCE_LOG.value})
_FLOOR_PLAN = frozenset({HouseCategory.FLOOR_PLAN.value})

SPEC = DomainSpec(
    domain=Domain.HOUSE,
    label="House",
    description=(
        "The home itself: appliance and outdoor-gear manuals, warranties and purchase invoices, "
        "maintenance records, floor plans and pipe/electrical layouts, and property ownership documents."
    ),
    home_url="/house",
    kinds=(
        KindSpec(HouseCategory.HOUSE_APPLIANCE.value, "House appliance",
                     "Manual or documentation for an indoor appliance (boiler, dishwasher, oven, heat pump...)."),
        KindSpec(HouseCategory.OUTDOOR_GEAR.value, "Outdoor gear",
                     "Manual or documentation for outdoor equipment (lawnmower, barbecue, garden tools...)."),
        KindSpec(HouseCategory.WARRANTY_INVOICE.value, "Warranty / invoice",
                     "A purchase invoice, receipt or warranty certificate for a household item."),
        KindSpec(HouseCategory.MAINTENANCE_LOG.value, "Maintenance log",
                     "A service, repair or inspection done on a household item — typed in by hand, "
                     "optionally with its receipt or report attached.", kind=SourceKind.RECORD),
        KindSpec(HouseCategory.FLOOR_PLAN.value, "Floor plan",
                     "A floor plan, or a diagram, photo or video of the house's pipes, sewage, wiring or floors.",
                     accepted_media=DEFAULT_MEDIA | {MediaKind.VIDEO}),
        KindSpec(HouseCategory.OWNERSHIP_DOCUMENT.value, "Ownership document",
                     "A property deed, land registry record or purchase contract for the house."),
    ),
    fields=(
        FieldSpec("item_name", "Item", FieldKind.SUGGEST, kinds=ITEM_CATEGORIES, required=True,
                  help="The same name groups the item's manual, warranty and maintenance records."),
        FieldSpec("room", "Room / location", FieldKind.SUGGEST, kinds=ITEM_CATEGORIES),
        FieldSpec("warranty_expiry", "Warranty expires", FieldKind.DATE, kinds=_WARRANTY,
                  help="Leave blank to have it read from the document (or assumed from the purchase date)."),
        FieldSpec("purchase_date", "Purchase / invoice date", FieldKind.DATE, kinds=_WARRANTY,
                  help="Used to assume Portugal's 3-year legal guarantee when no expiry date is stated."),
        FieldSpec("service_date", "Service date", FieldKind.DATE, kinds=_MAINTENANCE, required=True),
        FieldSpec("notes", "Notes", FieldKind.LONGTEXT, kinds=_MAINTENANCE),
        FieldSpec("system_type", "System", FieldKind.SUGGEST, kinds=_FLOOR_PLAN, required=True,
                  help="e.g. Pipes, Sewage, Electricity, Floor material."),
        FieldSpec("indoor_outdoor", "Indoor / outdoor", FieldKind.CHOICE, kinds=_FLOOR_PLAN, required=True,
                  choices=(("indoor", "Indoor"), ("outdoor", "Outdoor"))),
        FieldSpec("observations", "Observations", FieldKind.LONGTEXT, kinds=_FLOOR_PLAN,
                  media=frozenset({MediaKind.IMAGE, MediaKind.VIDEO}),
                  help="For photos and videos: what the picture shows."),
    ),
    handler=HouseHandler(),
    nav_links=(NavLink("Items & documents", "/house"),),
    overview_card=house_overview_card,
    document_url=lambda document: f"/house/documents/{document.id}",
    wiki=WikiSchema(
        entity_types=(
            EntityTypeSpec(
                page_type=ITEM_PAGE_TYPE, label="Items", key_field="item_name", kinds=ITEM_CATEGORIES,
                links=(LinkSpec("room", ROOM_PAGE_TYPE),),
                facts=(
                    FactSpec("type", "Type", "category", kinds=frozenset(ITEM_KIND_CATEGORIES)),
                    FactSpec("room", "Room / location", "room"),
                    FactSpec("warranty_expires", "Warranty expires", "effective_warranty_expiry",
                             kinds=_WARRANTY, policy=FactPolicy.LATEST, note_field="warranty_expiry_note"),
                    FactSpec("last_serviced", "Last serviced", "service_date",
                             kinds=_MAINTENANCE, policy=FactPolicy.LATEST),
                ),
            ),
            EntityTypeSpec(
                page_type=ROOM_PAGE_TYPE, label="Rooms", key_field="room",
                kinds=ITEM_CATEGORIES, facts=(),
            ),
        ),
    ),
    record_url=lambda record: f"/house/records/{record.id}",
    derive_fields=house_derived_fields,
)
