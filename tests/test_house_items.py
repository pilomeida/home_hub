import json
from datetime import date

from app.domains.house.items import (
    build_item_cards, describe_age, group_item_cards, house_documents, reference_sections,
)
from app.domains.house.overview import house_overview_card
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.services import wiki_store
from app.services.wiki_store import ClaimInput, PageRef

_counter = {"n": 0}


def _doc(session, category, fields, status=DocumentStatus.PROCESSED, domain=Domain.HOUSE, filename=None):
    _counter["n"] += 1
    n = _counter["n"]
    document = Document(
        filename=filename or f"doc{n}.pdf", file_path=f"/tmp/doc{n}.pdf", content_hash=f"hash-items-{n}",
        source=DocumentSource.MANUAL, status=status, domain=domain, category=category, fields_json=json.dumps(fields),
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def test_documents_about_one_item_share_a_card(session):
    _doc(session, "house_appliance", {"item_name": "boiler", "room": "Kitchen"})
    _doc(session, "warranty_invoice", {"item_name": "Boiler", "warranty_expiry": "2027-03-01"})
    _doc(session, "maintenance_log", {"item_name": "BOILER ", "service_date": "2025-11-02", "room": "Utility room"})
    _doc(session, "maintenance_log", {"item_name": "Boiler", "service_date": "2024-11-02"})
    _doc(session, "outdoor_gear", {"item_name": "Lawnmower"})

    cards = build_item_cards(session)

    assert [c.name for c in cards] == ["Boiler", "Lawnmower"]
    boiler = cards[0]
    assert boiler.key == "boiler" and len(boiler.documents) == 4
    assert boiler.kind == "house_appliance" and boiler.room == "Utility room"
    assert boiler.warranty_expiry == date(2027, 3, 1) and boiler.last_serviced == date(2025, 11, 2)
    assert boiler.anchor == "item-boiler"


def test_only_finalized_house_documents_count(session):
    _doc(session, "house_appliance", {"item_name": "Fridge"}, status=DocumentStatus.PENDING)
    _doc(session, "house_appliance", {"item_name": "Oven"}, status=DocumentStatus.NEEDS_ATTENTION)
    _doc(session, "bill", {}, domain=Domain.FINANCIALS)
    assert [d.fields_json for d in house_documents(session)] == ['{"item_name": "Oven"}']


def test_group_by_type_and_room(session):
    _doc(session, "house_appliance", {"item_name": "Boiler", "room": "Kitchen"})
    _doc(session, "outdoor_gear", {"item_name": "Lawnmower", "room": "garage"})
    _doc(session, "warranty_invoice", {"item_name": "TV"})
    cards = build_item_cards(session)

    by_type = [(title, [c.name for c in items]) for title, items in group_item_cards(cards, "type")]
    by_room = [(title, [c.name for c in items]) for title, items in group_item_cards(cards, "room")]

    assert by_type == [("House Appliances", ["Boiler"]), ("Outdoor Gear", ["Lawnmower"]), ("Other items", ["TV"])]
    assert by_room == [("garage", ["Lawnmower"]), ("Kitchen", ["Boiler"]), ("No room set", ["TV"])]


def test_type_view_always_shows_both_main_sections(session):
    assert group_item_cards([], "type") == [("House Appliances", []), ("Outdoor Gear", [])]


def test_reference_sections(session):
    _doc(session, "floor_plan", {"system_type": "Pipes", "indoor_outdoor": "outdoor"}, filename="pipes.pdf")
    _doc(session, "ownership_document", {}, filename="deed.pdf")
    sections = reference_sections(session)
    assert [(t, [h.document.filename for h in docs]) for t, docs in sections] == [
        ("Floor plans", ["pipes.pdf"]), ("Ownership documents", ["deed.pdf"]),
    ]
    assert sections[0][1][0].fields["system_type"] == "Pipes"


def test_item_card_links_its_wiki_page(session):
    document = _doc(session, "house_appliance", {"item_name": "Boiler"})
    wiki_store.apply_claims(session, [ClaimInput(PageRef("house.item", "Boiler", "boiler"), "type", "House appliance")], document=document)
    assert build_item_cards(session)[0].wiki_page_id is not None


def test_warranty_state_and_age():
    from app.domains.house.items import ItemCard

    today = date(2026, 9, 24)
    assert ItemCard(key="a", name="a").warranty_state(today) is None
    assert ItemCard(key="a", name="a", warranty_expiry=date(2026, 9, 1)).warranty_state(today) == "lapsed"
    assert ItemCard(key="a", name="a", warranty_expiry=date(2026, 10, 10)).warranty_state(today) == "due"
    assert ItemCard(key="a", name="a", warranty_expiry=date(2027, 1, 1)).warranty_state(today) == "ok"
    assert describe_age(date(2026, 9, 24), today) == "today"
    assert describe_age(date(2026, 9, 14), today) == "10 days ago"
    assert describe_age(date(2026, 3, 1), today) == "6 months ago"
    assert describe_age(date(2023, 9, 1), today) == "3 years ago"


def test_house_overview_card(session):
    today = date(2026, 9, 24)
    _doc(session, "warranty_invoice", {"item_name": "Boiler", "warranty_expiry": "2026-10-10"})
    _doc(session, "warranty_invoice", {"item_name": "Dishwasher", "warranty_expiry": "2026-11-15"})
    _doc(session, "warranty_invoice", {"item_name": "Oven", "warranty_expiry": "2030-01-01"})
    _doc(session, "warranty_invoice", {"item_name": "TV"})
    _doc(session, "maintenance_log", {"item_name": "Boiler", "service_date": "2026-03-01"})

    card = house_overview_card(session, today)

    texts = [line.text for line in card.lines]
    assert card.label == "House" and card.url == "/house"
    assert texts == [
        "Boiler warranty expires 10 Oct 2026",
        "Dishwasher warranty expires 15 Nov 2026",
        "1 warranty missing an expiry date",
        "Last maintenance: Boiler, 6 months ago",
        "5 documents on file",
    ]
    assert [line.attention for line in card.lines] == [True, False, True, False, False]
    assert card.lines[0].url == "/house#item-boiler"
