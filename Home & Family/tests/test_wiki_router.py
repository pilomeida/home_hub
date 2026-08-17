import json

from app.models.wiki import WikiChange, WikiPage


def test_list_wiki_pages_renders(client, session):
    session.add(WikiPage(topic="Electricity", facts_json=json.dumps({"provider": "EDP"})))
    session.commit()

    response = client.get("/wiki")

    assert response.status_code == 200
    assert "Electricity" in response.text


def test_wiki_page_detail_renders_facts_and_changes(client, session):
    page = WikiPage(topic="Electricity", facts_json=json.dumps({"provider": "EDP"}))
    session.add(page)
    session.commit()
    session.refresh(page)

    session.add(WikiChange(wiki_page_id=page.id, fact_key="provider", old_value=None, new_value="EDP"))
    session.commit()

    response = client.get(f"/wiki/{page.id}")

    assert response.status_code == 200
    assert "EDP" in response.text


def test_wiki_page_detail_404_for_missing_page(client):
    response = client.get("/wiki/9999")
    assert response.status_code == 404
