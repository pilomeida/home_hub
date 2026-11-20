"""PLAN step 10f: Papers, the one place to browse everything filed: newest first, a chip per category, and the
search (the same filed papers /search finds)."""

from datetime import datetime

from app.models.category import Category
from app.models.document import DocumentStatus
from app.services.paper_search import index_document
from tests.knowledge_factories import make_document


def _filed(session):
    out = [make_document(session, category=Category.HOUSE, filename="garden-hose-warranty.pdf",
                         fields={"item_name": "Garden hose"}, created_at=datetime(2026, 1, 5)),
           make_document(session, category=Category.HEALTH, paper_kind="lab_result", filename="blood-2026.pdf",
                         created_at=datetime(2026, 3, 1)),
           make_document(session, category=Category.OTHER, paper_kind="other", filename="school-letter.pdf",
                         created_at=datetime(2026, 2, 1))]
    make_document(session, category=Category.HOUSE, status=DocumentStatus.PENDING, filename="still-reading.pdf")
    for d in out:
        index_document(session, d)
    session.commit()
    return out


def test_everything_filed_newest_first_with_a_chip_per_category(client, session):
    _filed(session)
    html = client.get("/papers").text
    assert html.index("blood-2026.pdf") < html.index("school-letter.pdf") < html.index("garden-hose-warranty.pdf")
    assert "still-reading.pdf" not in html and "All (3)" in html
    assert 'href="/papers?category=health"' in html and html.index("?category=other") > html.index("?category=house")
    assert 'class="pill on" href="/papers"' in html


def test_a_chip_shows_one_category_and_search_stays_inside_it(client, session):
    _filed(session)
    house = client.get("/papers?category=house").text
    assert "garden-hose-warranty.pdf" in house and "blood-2026.pdf" not in house
    assert "garden-hose-warranty.pdf" in client.get("/papers?q=garden").text
    assert "No filed paper here has these words." in client.get("/papers?q=garden&category=health").text


def test_nothing_filed_says_so(client):
    assert "Nothing filed here yet." in client.get("/papers").text
