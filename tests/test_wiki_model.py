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


def test_wiki_page_domain_defaults_to_none(session):
    page = WikiPage(topic="Water — provider & contract", facts_json="{}")
    session.add(page)
    session.commit()
    session.refresh(page)

    assert page.domain is None


def test_wiki_page_domain_can_be_set(session):
    from app.models.domain import Domain

    page = WikiPage(
        topic="Electricity — provider & contract", facts_json='{"provider": "EDP"}',
        domain=Domain.FINANCIALS,
    )
    session.add(page)
    session.commit()
    session.refresh(page)

    assert page.domain == Domain.FINANCIALS
