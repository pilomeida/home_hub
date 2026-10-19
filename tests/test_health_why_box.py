"""Every health paper opens with a "Why this was done" box: what the paper says, or the person's own words."""

import json
from datetime import date

import pytest

from app.domains.fields import load_fields
from app.domains.health import extraction as ex
from app.domains.health import store
from app.models.document import DocumentStatus
from app.models.domain import Domain
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.knowledge_factories import make_document
from tests.test_health_context import CONTEXT_REPLY
from tests.test_health_extraction import _png


async def _filed(session, tmp_path, reply):
    document = make_document(session, domain=Domain.HEALTH, category="imaging_report", filename="doppler.pdf")
    got = await ex.extract_health_document(_png(tmp_path), None, gateway=FakeGateway([gateway_text_result(json.dumps(reply))]))
    store.apply_extraction(session, document, got)
    return document


def _why_box(html):
    """Only the text of the "Why this was done" box: from its opening tag to the grid that follows it."""
    assert 'class="why-box"' in html
    return html.split('class="why-box"', 1)[1].split('<div style="display:grid', 1)[0]


@pytest.mark.asyncio
async def test_the_box_shows_the_papers_reason_notes_and_people(client, session, tmp_path):
    doc = await _filed(session, tmp_path, CONTEXT_REPLY)
    box = _why_box(client.get(f"/health/documents/{doc.id}").text)
    assert "Why this was done" in box
    assert "Avaliação da Circulação Cervicoencefálica" in box and "AVC isquémico aos 40 anos" in box
    assert "Requested by" in box and "C.E Neurologia" in box
    assert "Done by" in box and "Cpl. Dra. Mª Fátima Soares" in box
    assert "<details><summary>Details</summary>" in box and "Exame Nº: 12935" in box.split("<summary>Details</summary>", 1)[1]


@pytest.mark.asyncio
async def test_a_paper_that_gives_no_reason_invites_the_person_to_write_one(client, session, tmp_path):
    reply = {k: v for k, v in CONTEXT_REPLY.items() if k != "indication"}
    doc = await _filed(session, tmp_path, reply)
    assert "The paper does not say why it was made. Write it here." in client.get(f"/health/documents/{doc.id}").text


@pytest.mark.asyncio
async def test_a_persons_own_reason_is_saved_and_shown(client, session, tmp_path):
    reply = {k: v for k, v in CONTEXT_REPLY.items() if k != "indication"}
    doc = await _filed(session, tmp_path, reply)
    r = client.post(f"/health/documents/{doc.id}/why", data={"indication": "Controlo anual do AVC de 2015"}, follow_redirects=False)
    assert r.status_code == 303
    session.refresh(doc)
    assert load_fields(doc)["indication"] == "Controlo anual do AVC de 2015"
    html = client.get(f"/health/documents/{doc.id}").text
    assert "Controlo anual do AVC de 2015" in _why_box(html)
    assert "The paper does not say why it was made" not in html


def test_writing_a_reason_for_a_non_health_paper_is_a_404(client, session):
    other = make_document(session, domain=Domain.HOUSE, category="house_appliance")
    assert client.post(f"/health/documents/{other.id}/why", data={"indication": "x"}).status_code == 404


def _paper_with_status(session, status):
    document = make_document(session, domain=Domain.HEALTH, category="imaging_report", filename="doppler.pdf")
    document.status = status
    session.add(document)
    session.commit()
    return document


def test_a_paper_still_being_read_does_not_claim_the_paper_has_no_reason(client, session):
    doc = _paper_with_status(session, DocumentStatus.PENDING)
    box = _why_box(client.get(f"/health/documents/{doc.id}").text)
    assert "Being read — the reason will appear here when the reading is done." in box
    assert "does not say why" not in box and "<textarea" not in box and "Save" not in box


def test_a_paper_that_could_not_be_read_says_so_and_offers_no_textarea(client, session):
    doc = _paper_with_status(session, DocumentStatus.NEEDS_ATTENTION)
    box = _why_box(client.get(f"/health/documents/{doc.id}").text)
    assert "Not read, so the Hub does not know why it was made. Read it again or write the reason in the boxes below." in box
    assert "does not say why" not in box and "<textarea" not in box and "Save" not in box


def test_saving_a_reason_while_the_paper_is_being_read_is_refused(client, session):
    doc = _paper_with_status(session, DocumentStatus.PENDING)
    r = client.post(f"/health/documents/{doc.id}/why", data={"indication": "x"}, follow_redirects=False)
    assert r.status_code == 409
    assert r.json()["detail"] == {"what": "Not saved.", "why": "The paper is still being read, and the reading would overwrite it.",
                                  "next_step": "Wait until the reading is done, then write it again."}
    session.refresh(doc)
    assert "indication" not in load_fields(doc)


def test_saving_a_reason_for_a_paper_that_was_not_read_is_refused(client, session):
    doc = _paper_with_status(session, DocumentStatus.NEEDS_ATTENTION)
    r = client.post(f"/health/documents/{doc.id}/why", data={"indication": "x"}, follow_redirects=False)
    assert r.status_code == 409
    assert r.json()["detail"] == {"what": "Not saved.", "why": "The paper has not been read.",
                                  "next_step": "Read it again, or write the reason in the boxes below."}


def test_saving_a_reason_for_a_deleted_paper_is_refused(client, session):
    doc = _paper_with_status(session, DocumentStatus.DISCARDED)
    r = client.post(f"/health/documents/{doc.id}/why", data={"indication": "x"}, follow_redirects=False)
    assert r.status_code == 409
    assert r.json()["detail"] == {"what": "Not saved.", "why": "This paper was deleted.", "next_step": "Upload it again if you need it."}
