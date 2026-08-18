from datetime import date

from app.models.document import Document, DocumentSource
from app.models.utility_reading import UtilityReading, UtilityType


def test_create_and_read_utility_reading(session):
    document = Document(
        filename="edp-july.pdf", file_path="/tmp/edp-july.pdf",
        content_hash="hash-elec-1", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    reading = UtilityReading(
        document_id=document.id,
        utility_type=UtilityType.ELECTRICITY,
        period_label="2026-07",
        billing_period_start=date(2026, 6, 26),
        billing_period_end=date(2026, 7, 25),
        invoice_number="FA CO26/42 105",
        consumption_value=401.0,
        consumption_unit="kWh",
        cost_total=85.0,
        cost_per_unit=85.0 / 401.0,
        energy_cost=56.5,
        power_cost=4.54,
        fees_taxes_cost=12.99,
        vat_cost=10.97,
    )
    session.add(reading)
    session.commit()
    session.refresh(reading)

    fetched = session.get(UtilityReading, reading.id)
    assert fetched.utility_type == UtilityType.ELECTRICITY
    assert fetched.period_label == "2026-07"
    assert fetched.consumption_value == 401.0
    assert fetched.consumption_unit == "kWh"
    assert fetched.cost_total == 85.0
    assert fetched.energy_cost == 56.5


def test_utility_reading_optional_fields_default_to_none(session):
    document = Document(
        filename="water-bill.pdf", file_path="/tmp/water-bill.pdf",
        content_hash="hash-water-1", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    reading = UtilityReading(
        document_id=document.id,
        utility_type=UtilityType.WATER,
        period_label="2026-07",
        cost_total=20.0,
    )
    session.add(reading)
    session.commit()
    session.refresh(reading)

    fetched = session.get(UtilityReading, reading.id)
    assert fetched.billing_period_start is None
    assert fetched.invoice_number is None
    assert fetched.consumption_value is None
    assert fetched.cost_per_unit is None
    assert fetched.energy_cost is None
