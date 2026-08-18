"""UtilityReading: consumption + cost-breakdown detail for a utility bill,
beyond what the generic Transaction model tracks."""

from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class UtilityType(str, Enum):
    ELECTRICITY = "electricity"
    WATER = "water"
    TELECOM = "telecom"


class UtilityReading(SQLModel, table=True):
    __tablename__ = "utility_readings"

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", index=True)
    utility_type: UtilityType
    period_label: str = Field(index=True)
    billing_period_start: Optional[date] = None
    billing_period_end: Optional[date] = None
    invoice_number: Optional[str] = None
    consumption_value: Optional[float] = None
    consumption_unit: Optional[str] = None
    cost_total: float
    cost_per_unit: Optional[float] = None
    energy_cost: Optional[float] = None
    power_cost: Optional[float] = None
    fees_taxes_cost: Optional[float] = None
    vat_cost: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
