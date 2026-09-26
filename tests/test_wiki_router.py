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


from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.wiki import WikiOperation
from app.services import wiki_store
from app.services.wiki_store import ClaimInput, PageRef


def _house_document(session, name="boiler-warranty.pdf", content_hash="hw1"):
    document = Document(filename=name, file_path=f"/srv/docs/{content_hash}.pdf", content_hash=content_hash,
                        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED, domain=Domain.HOUSE)
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def test_wiki_index_groups_and_summarises(client, session, fake_domain):
    ref = PageRef("fake.item", "Boiler", "boiler")
    wiki_store.apply_claims(session, [ClaimInput(ref, "room", "Kitchen", label="Room")], document=_house_document(session))

    response = client.get("/wiki")

    assert "Fake" in response.text and "Items" in response.text
    assert "Room: Kitchen" in response.text
    assert 'href="/wiki/log"' in response.text


def test_wiki_index_and_page_humanize_a_claim_missing_a_label(client, session, fake_domain):
    ref = PageRef("fake.item", "Boiler", "boiler")
    wiki_store.apply_claims(session, [ClaimInput(ref, "statement_period", "2026-08")], document=_house_document(session))
    page = wiki_store.find_page(session, ref)

    index_response = client.get("/wiki")
    page_response = client.get(f"/wiki/{page.id}")

    assert "Statement period: 2026-08" in index_response.text
    assert "statement_period" not in index_response.text
    assert "Statement period" in page_response.text
    assert "<td>statement_period</td>" not in page_response.text


def test_wiki_page_shows_claims_sources_and_superseded_history(client, session, fake_domain):
    ref = PageRef("fake.item", "Boiler", "boiler")
    first = _house_document(session)
    second = _house_document(session, "boiler-extended.pdf", "hw2")
    wiki_store.apply_claims(session, [ClaimInput(ref, "warranty_expires", "2027-01-01", label="Warranty expires")], document=first)
    wiki_store.apply_claims(session, [ClaimInput(ref, "warranty_expires", "2029-01-01", label="Warranty expires")], document=second)
    page = wiki_store.find_page(session, ref)

    response = client.get(f"/wiki/{page.id}")

    assert response.status_code == 200
    assert "2029-01-01" in response.text
    assert "boiler-extended.pdf" in response.text
    assert f'href="/fake/documents/{second.id}"' in response.text
    assert "Superseded" in response.text and "2027-01-01" in response.text


def test_wiki_log_lists_operations(client, session):
    wiki_store.append_log(session, WikiOperation.INGEST, "boiler-warranty.pdf → Boiler")

    response = client.get("/wiki/log")

    assert response.status_code == 200
    assert "boiler-warranty.pdf → Boiler" in response.text
    assert "ingest" in response.text


def test_wiki_page_lists_links_both_ways(client, session, fake_domain):
    from app.services.wiki_store import LinkInput

    kitchen = PageRef("fake.room", "Kitchen", "kitchen")
    boiler = PageRef("fake.item", "Boiler", "boiler")
    wiki_store.apply_claims(session, [], document=_house_document(session), links=[LinkInput(boiler, kitchen)])

    response = client.get(f"/wiki/{wiki_store.find_page(session, kitchen).id}")

    assert "Linked pages" in response.text and "Boiler" in response.text
