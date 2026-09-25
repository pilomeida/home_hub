def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Overview" in response.text
    assert "Financials" in response.text
    assert "Bills &amp; Bank" in response.text  # Jinja autoescapes the "&" in the registry label
    assert 'href="/financials/bills"' in response.text
    assert 'href="/financials/transactions"' in response.text
    assert 'href="/financials/utilities/electricity"' in response.text


def test_nav_is_built_from_the_registry(client, fake_domain):
    response = client.get("/todos")

    assert response.status_code == 200
    assert 'href="/fake"' in response.text
    assert "Fake home" in response.text
    # Financials is not in the fake registry, so its links are gone.
    assert 'href="/financials/bills"' not in response.text
