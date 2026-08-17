from app.models.wiki import WikiChange, WikiPage


def test_create_wiki_page_and_change(session):
    page = WikiPage(topic="Electricity — provider & contract", facts_json='{"provider": "EDP"}')
    session.add(page)
    session.commit()
    session.refresh(page)

    change = WikiChange(
        wiki_page_id=page.id, fact_key="provider", old_value=None, new_value="EDP",
    )
    session.add(change)
    session.commit()
    session.refresh(change)

    assert page.id is not None
    assert change.wiki_page_id == page.id
