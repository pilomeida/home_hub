def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_renders(client):
    response = client.get("/overview")
    assert response.status_code == 200
    assert "Overview" in response.text
    assert "Financials" in response.text
    # Since step 10b a section's pages show on that section's pages (and all of them on More), from the registry.
    money = client.get("/financials/transactions").text
    assert "Bills &amp; Bank" in money  # Jinja autoescapes the "&" in the registry label
    assert 'href="/financials/bills"' in money
    assert 'href="/financials/transactions"' in money
    assert 'href="/financials/utilities/electricity"' in money


def test_nav_is_built_from_the_registry(client, fake_category):
    response = client.get("/more")                     # every page the menu reaches (step 10b)

    assert response.status_code == 200
    assert 'href="/fake"' in response.text
    assert "Fake home" in response.text
    # Financials is not in the fake registry, so its links are gone.
    assert 'href="/financials/bills"' not in response.text
