"""Vehicle paper kinds (PLAN step 8c). Every paper names its car by the plate; the car is a subject (a thing) of the
registry. The car's insurance stays under Insurance and shows on the car's card; a road-tax notice that asks for money
is paid like any bill, and the proof of payment is filed here with the next year's date."""

from enum import Enum


class VehicleKind(str, Enum):
    REGISTRATION = "registration"
    INSPECTION = "inspection"
    ROAD_TAX = "road_tax"
    SERVICE = "service"


LABELS = {
    VehicleKind.REGISTRATION.value: "Registration",
    VehicleKind.INSPECTION.value: "Inspection (IPO)",
    VehicleKind.ROAD_TAX.value: "Road tax (IUC)",
    VehicleKind.SERVICE.value: "Service",
}

# Days before each date that its To-Do comes (Claude, 2026-11-20; Pedro may change).
INSPECTION_BEFORE, ROAD_TAX_BEFORE, SERVICE_BEFORE = 30, 14, 21
