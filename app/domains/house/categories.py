"""House document categories (stored in Document.category) and groupings."""

from enum import Enum


class HouseCategory(str, Enum):
    HOUSE_APPLIANCE = "house_appliance"
    OUTDOOR_GEAR = "outdoor_gear"
    WARRANTY_INVOICE = "warranty_invoice"
    MAINTENANCE_LOG = "maintenance_log"
    FLOOR_PLAN = "floor_plan"
    OWNERSHIP_DOCUMENT = "ownership_document"


# Categories whose documents belong to one item (grouped by item_name).
ITEM_CATEGORIES = frozenset({
    HouseCategory.HOUSE_APPLIANCE.value, HouseCategory.OUTDOOR_GEAR.value,
    HouseCategory.WARRANTY_INVOICE.value, HouseCategory.MAINTENANCE_LOG.value,
})
# Categories that say what KIND of item it is (drives the landing-page section).
ITEM_KIND_CATEGORIES = (HouseCategory.HOUSE_APPLIANCE.value, HouseCategory.OUTDOOR_GEAR.value)
# Non-item documents, shown in the Reference section.
REFERENCE_CATEGORIES = (HouseCategory.FLOOR_PLAN.value, HouseCategory.OWNERSHIP_DOCUMENT.value)

ITEM_PAGE_TYPE = "house.item"
