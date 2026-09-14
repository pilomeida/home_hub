def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Overview" in response.text
    assert "Financials" in response.text
    assert "Bills & Bank" in response.text
    assert 'href="/financials/bills"' in response.text
    assert 'href="/financials/transactions"' in response.text
    assert 'href="/financials/utilities/electricity"' in response.text
