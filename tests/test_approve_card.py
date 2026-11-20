"""PLAN step 10h: a paper added from the page (the phone's +) goes to the Inbox as one sent by email or Telegram, then
opens a light approve card: the file, where the Hub would file it, its key fields, Approve / Edit / Later, using the
Inbox's own approval."""

import pytest

from app.main import app
from app.models.category import Category
from app.models.document import Document, DocumentStatus
from app.routers.inbox import get_inbox_classifier
from app.services.category_classifier import CategorySuggestion
from tests.fakes.text_pdf import text_pdf_bytes

PDF = text_pdf_bytes(["AEG dishwasher invoice", "Total 649.00 EUR"])


@pytest.fixture
def classifier():
    seen = {}

    def use(category, kind, confidence=0.95):
        async def classify(path, context):
            seen["context"] = context
            return CategorySuggestion(category, kind, confidence, "looks like an appliance invoice")
        app.dependency_overrides[get_inbox_classifier] = lambda: classify
        return seen
    yield use
    app.dependency_overrides.pop(get_inbox_classifier, None)


def _add(client, name="aeg.pdf", content=PDF, note="for the kitchen"):
    return client.post("/inbox/add", files={"file": (name, content, "application/pdf")}, data={"note": note},
                       follow_redirects=False)


def test_added_paper_opens_its_card_and_approves_with_the_inbox_rules(client, session, inbox_documents_dir, classifier):
    seen = classifier(Category.HOUSE, "house_appliance")
    r = _add(client)
    assert r.status_code == 303 and r.headers["location"].endswith("/card")
    assert seen["context"] == "for the kitchen"
    card = client.get(r.headers["location"]).text
    assert "Filed as" in card and 'name="item_name"' in card and ">Approve<" in card and ">Later<" in card
    doc_id = int(r.headers["location"].split("/")[2])
    refused = client.post(f"/inbox/{doc_id}/card", data={"domain": "house", "category": "house_appliance", "item_name": ""}).text
    assert "needs-attention" in refused and session.get(Document, doc_id).status == DocumentStatus.PENDING_REVIEW
    done = client.post(f"/inbox/{doc_id}/card", data={"domain": "house", "category": "house_appliance", "item_name": "Dishwasher"}).text
    session.expire_all()
    assert "Filed as" in done and session.get(Document, doc_id).status == DocumentStatus.PROCESSED


def test_an_unsure_paper_sends_you_to_choose(client, session, inbox_documents_dir, classifier):
    classifier(None, None, 0.0)
    card = client.get(_add(client).headers["location"]).text
    assert "Not sure where this belongs" in card and "Choose where it goes" in card and ">Approve<" not in card


def test_the_same_paper_twice_is_not_added_again(client, session, inbox_documents_dir, classifier):
    classifier(Category.HOUSE, "house_appliance")
    first = _add(client).headers["location"]
    again = _add(client, name="aeg-copy.pdf").headers["location"]
    assert again.startswith(first) and again.endswith("duplicate=1")
    assert "already waiting in the Inbox" in client.get(again).text


@pytest.mark.parametrize("name,content,expect", [("aeg.pdf", b"", "No file was chosen"),
                                                 ("notes.docx", b"PK..", "only takes PDFs, photos and short videos")])
def test_a_file_that_cannot_be_added_says_why(client, inbox_documents_dir, classifier, name, content, expect):
    classifier(Category.HOUSE, "house_appliance")
    r = _add(client, name=name, content=content)
    assert r.status_code == 303 and expect in client.get(r.headers["location"]).text
