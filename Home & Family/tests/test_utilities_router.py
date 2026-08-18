from app.models.document import Document, DocumentSource
from app.models.utility_reading import UtilityReading, UtilityType


def _make_document(session, content_hash):
    document = Document(
        filename="edp.pdf", file_path="/tmp/edp.pdf", content_hash=content_hash,
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def test_utilities_root_redirects_to_electricity(client):
    response = client.get("/utilities", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/utilities/electricity"


def test_unknown_tab_returns_404(client):
    response = client.get("/utilities/gas")
    assert response.status_code == 404


def test_water_tab_renders_empty_state_with_no_data(client):
    response = client.get("/utilities/water")
    assert response.status_code == 200
    assert "No data yet" in response.text


def test_electricity_tab_renders_readings(client, session):
    document = _make_document(session, "hash-elec-1")
    session.add(UtilityReading(
        document_id=document.id, utility_type=UtilityType.ELECTRICITY,
        period_label="2026-07", consumption_value=401.0, consumption_unit="kWh",
        cost_total=85.0, cost_per_unit=85.0 / 401.0,
        energy_cost=56.5, power_cost=4.54, fees_taxes_cost=12.99, vat_cost=10.97,
    ))
    session.commit()

    response = client.get("/utilities/electricity")

    assert response.status_code == 200
    assert "1 reading" in response.text
