from datetime import date

from app.domains.health import areas as areas_svc
from app.models.health import HealthMeasurement
from sqlmodel import select
from tests.test_health_browse import _paper


def test_the_landing_page_lists_papers_with_facets(client, session):
    _paper(session, "Maria Santos Almeida", date(2026, 3, 12), "test", ["Eyes"], ["Glaucoma"], name="eye.pdf")
    html = client.get("/health/records").text
    assert "eye.pdf" in html and "Maria Santos Almeida" in html and "Eyes" in html and "2026" in html


def test_the_filters_work_through_the_url(client, session):
    _paper(session, "Maria Santos Almeida", date(2026, 3, 12), "test", ["Eyes"], name="eye.pdf")
    _paper(session, "Pedro Santos Almeida", date(2024, 1, 5), "check_up", ["Heart"], name="heart.pdf")
    html = client.get("/health/records?year=2024").text
    assert "heart.pdf" in html and "eye.pdf" not in html


def test_the_detail_page_shows_each_value_beside_the_original(client, session):
    doc = _paper(session, "Maria Santos Almeida", date(2026, 3, 12), "test", ["Eyes"], name="eye.pdf")
    html = client.get(f"/health/documents/{doc.id}").text
    assert "LDL" in html and f"/static/documents/" in html


def test_a_non_health_document_is_a_404(client, session):
    from app.models.domain import Domain
    from tests.knowledge_factories import make_document
    other = make_document(session, domain=Domain.HOUSE, category="house_appliance")
    assert client.get(f"/health/documents/{other.id}").status_code == 404


def test_marking_a_value_as_checked_clears_the_flag(client, session):
    doc = _paper(session, "Ana Lima", date(2026, 1, 1), "test", [], doubtful=True, name="d.pdf")
    m = session.exec(select(HealthMeasurement)).first()
    assert m.needs_check
    r = client.post(f"/health/documents/{doc.id}/measurements/{m.id}/ok", follow_redirects=False)
    assert r.status_code == 303
    session.refresh(m)
    assert m.needs_check is False


def test_retagging_a_paper(client, session):
    doc = _paper(session, "Ana Lima", date(2026, 1, 1), "test", [], name="d.pdf")
    r = client.post(f"/health/documents/{doc.id}/tags", data={"areas": "Eyes, Skin", "conditions": "Glaucoma"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert {a.name for a in session.exec(select(areas_svc.HealthArea)).all()} == {"Eyes", "Skin", "Glaucoma"}


def test_areas_page_approves_renames_and_merges(client, session):
    a = areas_svc.resolve_area(session, "Digestive", "area")
    b = areas_svc.resolve_area(session, "Digestive system", "area")
    assert "Digestive" in client.get("/health/areas").text
    assert client.post(f"/health/areas/{a.id}/approve", follow_redirects=False).status_code == 303
    assert client.post(f"/health/areas/{a.id}/merge", data={"into": str(b.id)}, follow_redirects=False).status_code == 303
    session.refresh(a)
    assert a.merged_into_id == b.id
    r = client.post(f"/health/areas/{b.id}/rename", data={"name": "  "})
    assert r.status_code == 400 and "Not renamed." in r.text


def test_people_page_confirms_and_merges(client, session):
    _paper(session, "Maria Santos Almeida", date(2026, 3, 12), "test", [], name="a.pdf")
    _paper(session, "Mariah Almeida", date(2026, 3, 13), "test", [], name="b.pdf")
    from app.models.health import HealthPerson
    ids = [p.id for p in session.exec(select(HealthPerson).order_by(HealthPerson.id)).all()]
    assert client.post(f"/health/people/{ids[0]}/confirm", follow_redirects=False).status_code == 303
    assert client.post(f"/health/people/{ids[1]}/merge", data={"into": str(ids[0])}, follow_redirects=False).status_code == 303
    from app.domains.health import people as people_svc
    assert [p.id for p in people_svc.all_people(session)] == [ids[0]]
    assert "Mariah Almeida" not in client.get("/health/people").text


def test_a_merge_sticks_when_a_paper_is_edited_or_a_value_is_marked_right(client, session):
    from app.domains.fields import load_fields
    from app.domains.health import people as people_svc, store
    from app.models.health import HealthPerson
    doc = _paper(session, "Pedro S. Almeida", date(2026, 3, 12), "test", [], doubtful=True, name="a.pdf")
    _paper(session, "Pedro Santos Almeida", date(2026, 3, 13), "test", [], name="b.pdf")
    old = session.exec(select(HealthPerson).where(HealthPerson.name == "Pedro S. Almeida")).one()
    keep = session.exec(select(HealthPerson).where(HealthPerson.name == "Pedro Santos Almeida")).one()
    assert client.post(f"/health/people/{old.id}/merge", data={"into": str(keep.id)}, follow_redirects=False).status_code == 303
    session.expire_all()                                              # the merge ran in the route's own session
    assert load_fields(doc)["person"] == "Pedro S. Almeida"           # the paper still holds the old spelling
    entry = store.sync_entry_from_fields(session, doc)                # an edit of any field syncs again
    assert entry.person_id == keep.id
    m = session.exec(select(HealthMeasurement)).first()
    assert client.post(f"/health/documents/{doc.id}/measurements/{m.id}/ok", follow_redirects=False).status_code == 303
    session.expire_all()
    assert store.entry_for(session, doc.id).person_id == keep.id
    assert [p.id for p in people_svc.all_people(session)] == [keep.id]
    assert len(session.exec(select(HealthPerson)).all()) == 2          # the alias row, no third person
    assert "Pedro S. Almeida" not in client.get("/health/people").text
    assert "Pedro S. Almeida" not in client.get("/health/records").text


def test_upload_files_a_paper_through_the_real_pipeline(client, session, monkeypatch):
    import json
    from app.llm_gateway import set_gateway_provider
    from tests import privacy_world as pw
    from tests.fakes.fake_gateway import gateway_text_result
    from tests.test_health_extraction import REPLY
    from tests.test_health_handler import WIKI_REPLY
    wire = pw.Wire()
    wire.script = [gateway_text_result(json.dumps(REPLY)), gateway_text_result(json.dumps(WIKI_REPLY))]
    set_gateway_provider(lambda: wire.client())
    try:
        r = client.post("/health/upload", data={"category": "lab_result"},
                        files={"file": ("lab.png", b"\x89PNG\r\n\x1a\nzz", "image/png")}, follow_redirects=False)
    finally:
        set_gateway_provider(None)
    assert r.status_code == 303 and r.headers["location"].startswith("/health/documents/")
    assert [b["sensitivity"] for b in wire.parsed()] == ["house_high", "house_high"]


def test_health_stays_the_liveness_check_and_the_records_page_is_separate(client, session):
    live = client.get("/health")
    assert live.status_code == 200 and live.json() == {"status": "ok"}
    records = client.get("/health/records")
    assert records.status_code == 200
    assert "application/json" not in records.headers["content-type"]
    assert "Health" in records.text and "status" not in records.text[:20]


def test_tagging_a_paper_that_has_no_record_yet_is_a_409(client, session):
    from app.models.domain import Domain
    from tests.knowledge_factories import make_document
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result")
    r = client.post(f"/health/documents/{doc.id}/tags", data={"areas": "Eyes"})
    assert r.status_code == 409
    assert r.json()["detail"]["what"] == "Not tagged."


def test_a_measurement_of_another_paper_is_a_404(client, session):
    a = _paper(session, "Ana Lima", date(2026, 1, 1), "test", [], doubtful=True, name="a.pdf")
    b = _paper(session, "Ana Lima", date(2026, 1, 2), "test", [], name="b.pdf")
    m = session.exec(select(HealthMeasurement)).first()
    assert client.post(f"/health/documents/{b.id}/measurements/{m.id}/ok").status_code == 404
    session.refresh(m)
    assert m.needs_check


def test_posting_to_a_non_health_document_is_a_404(client, session):
    from app.models.domain import Domain
    from tests.knowledge_factories import make_document
    other = make_document(session, domain=Domain.HOUSE, category="house_appliance")
    assert client.post(f"/health/documents/{other.id}/tags", data={"areas": "Eyes"}).status_code == 404
    assert client.post(f"/health/documents/{other.id}/fields", data={}).status_code == 404
    assert client.post(f"/health/documents/{other.id}/measurements/1/ok").status_code == 404


def test_merge_without_a_choice_explains_itself_instead_of_a_422(client, session):
    a = areas_svc.resolve_area(session, "Digestive", "area")
    for data in ({}, {"into": ""}, {"into": "abc"}):
        r = client.post(f"/health/areas/{a.id}/merge", data=data)
        assert r.status_code == 400 and "Not merged." in r.text and "Nothing was chosen" in r.text
    _paper(session, "Ana Lima", date(2026, 1, 1), "test", [], name="a.pdf")
    from app.models.health import HealthPerson
    p = session.exec(select(HealthPerson)).first()
    r = client.post(f"/health/people/{p.id}/merge", data={})
    assert r.status_code == 400 and "Nothing was chosen" in r.text and "Pick the entry to keep" in r.text


def test_merging_into_an_area_that_was_merged_away_is_refused(client, session):
    a = areas_svc.resolve_area(session, "Digestive", "area")
    b = areas_svc.resolve_area(session, "Digestive system", "area")
    c = areas_svc.resolve_area(session, "Gut", "area")
    assert client.post(f"/health/areas/{a.id}/merge", data={"into": str(b.id)}, follow_redirects=False).status_code == 303
    r = client.post(f"/health/areas/{c.id}/merge", data={"into": str(a.id)})
    assert r.status_code == 400 and "Not merged." in r.text and "already merged" in r.text
    session.refresh(c)
    assert c.merged_into_id is None


def test_merging_an_area_into_itself_or_another_kind_is_refused(client, session):
    a = areas_svc.resolve_area(session, "Eyes", "area")
    k = areas_svc.resolve_area(session, "Glaucoma", "condition")
    assert client.post(f"/health/areas/{a.id}/merge", data={"into": str(a.id)}).status_code == 400
    r = client.post(f"/health/areas/{a.id}/merge", data={"into": str(k.id)})
    assert r.status_code == 400 and "separate lists" in r.text
    session.refresh(a)
    assert a.merged_into_id is None


def test_stale_ids_get_an_explanation_not_a_silent_redirect(client, session):
    for url, data in (("/health/areas/9999/approve", {}), ("/health/areas/9999/merge", {"into": "1"}),
                      ("/health/people/9999/confirm", {}), ("/health/people/9999/merge", {"into": "1"})):
        r = client.post(url, data=data, follow_redirects=False)
        assert r.status_code == 404 and "no longer exists" in r.text and "Reload the page" in r.text, url


def test_a_lone_person_has_no_merge_form(client, session):
    _paper(session, "Ana Lima", date(2026, 1, 1), "test", [], name="a.pdf")
    html = client.get("/health/people").text
    assert "Ana Lima" in html and "/merge" not in html
    _paper(session, "Pedro Santos Almeida", date(2026, 1, 2), "test", [], name="b.pdf")
    assert "/merge" in client.get("/health/people").text


def test_the_landing_page_pluralises_people_to_confirm(client, session):
    _paper(session, "Ana Lima", date(2026, 1, 1), "test", [], name="a.pdf")
    assert "1 person to confirm" in client.get("/health/records").text
    _paper(session, "Pedro Santos Almeida", date(2026, 1, 2), "test", [], name="b.pdf")
    assert "2 people to confirm" in client.get("/health/records").text


def test_the_upload_page_shows_the_scan_notice_from_the_template_globals(client):
    from markupsafe import escape
    from app.privacy.notices import SCAN_NOTICE
    assert str(escape(SCAN_NOTICE)) in client.get("/health/upload").text


def _upload_only_the_file(client, replies):
    """The page asks for the file alone; the reading (scripted, as the model would return it) proposes the rest."""
    import json
    from app.llm_gateway import set_gateway_provider
    from tests import privacy_world as pw
    from tests.fakes.fake_gateway import gateway_text_result
    wire = pw.Wire()
    wire.script = [gateway_text_result(json.dumps(r)) for r in replies]
    set_gateway_provider(lambda: wire.client())
    try:
        r = client.post("/health/upload", files={"file": ("paper.png", b"\x89PNG\r\n\x1a\nyy", "image/png")},
                        follow_redirects=False)
    finally:
        set_gateway_provider(None)
    return r, wire


def test_the_upload_page_asks_only_for_the_file_and_states_the_level(client):
    from markupsafe import escape
    from app.privacy.notices import HEALTH_NOTICE, SCAN_NOTICE
    page = client.get("/health/upload").text
    assert 'name="file"' in page and 'name="category"' not in page and 'name="person"' not in page
    assert str(escape(SCAN_NOTICE)) in page and str(escape(HEALTH_NOTICE)) in page
    assert "house_high" in page and "Privatemode" in page


def test_the_reading_proposes_the_kind_of_paper_and_it_is_filed_at_high(client, session):
    from app.models.document import Document, DocumentStatus
    from tests.test_health_extraction import REPLY
    from tests.test_health_handler import WIKI_REPLY
    r, wire = _upload_only_the_file(client, [dict(REPLY, category="lab_result"), WIKI_REPLY])
    assert r.status_code == 303
    session.expire_all()
    doc = session.exec(select(Document)).one()
    assert doc.category == "lab_result" and doc.sensitivity_tier == "house_high"
    assert doc.status == DocumentStatus.PROCESSED
    assert [b["sensitivity"] for b in wire.parsed()] == ["house_high", "house_high"]


def test_a_paper_the_reading_cannot_place_waits_for_a_person_to_choose_its_kind(client, session):
    from app.models.document import Document, DocumentStatus
    from tests.test_health_extraction import REPLY
    r, wire = _upload_only_the_file(client, [dict(REPLY, category=None)])
    assert r.status_code == 303
    session.expire_all()
    doc = session.exec(select(Document)).one()
    assert doc.status == DocumentStatus.NEEDS_ATTENTION and doc.category is None
    assert "what kind of health paper" in doc.failure_reason
    assert [b["sensitivity"] for b in wire.parsed()] == ["house_high"]          # nothing went out below High
    page = client.get(r.headers["location"]).text
    assert "Kind of paper not known yet" in page and f"/documents/{doc.id}/edit" in page


def _stored_health_paper(session, status, reason=None):
    from app.models.document import DocumentStatus
    from app.models.domain import Domain
    from tests.knowledge_factories import make_document
    doc = make_document(session, domain=Domain.HEALTH, category=None)
    doc.status, doc.failure_reason = DocumentStatus(status), reason
    session.add(doc)
    session.commit()
    return doc


def test_a_paper_still_being_read_says_so_and_refreshes_itself(client, session):
    doc = _stored_health_paper(session, "pending")
    page = client.get(f"/health/documents/{doc.id}").text
    assert "Being read." in page and 'http-equiv="refresh"' in page and "Read again" in page
    assert "being read" in client.get("/health/records").text


def test_a_paper_that_failed_shows_why_and_can_be_read_again(client, session, tmp_path):
    from app.llm_gateway import GatewayError
    from app.models.document import Document, DocumentStatus
    from tests.test_health_extraction import REPLY
    from tests.test_health_handler import WIKI_REPLY
    reason = f"Health processing failed: {GatewayError(502, 'schema_rejected: output cut off')}"
    doc = _stored_health_paper(session, "needs_attention", reason)
    paper = tmp_path / "paper.png"
    paper.write_bytes(b"\x89PNG\r\n\x1a\nzz")
    doc.filename, doc.file_path = "paper.png", str(paper)
    session.add(doc)
    session.commit()
    page = client.get(f"/health/documents/{doc.id}").text
    assert "Not read." in page and "Read again" in page
    assert "being read" not in client.get("/health/records").text and "needs you" in client.get("/health/records").text
    # pressing the button reads it again through the real pipeline
    import json
    from app.llm_gateway import set_gateway_provider
    from tests import privacy_world as pw
    from tests.fakes.fake_gateway import gateway_text_result
    wire = pw.Wire()
    wire.script = [gateway_text_result(json.dumps(dict(REPLY, category="lab_result"))), gateway_text_result(json.dumps(WIKI_REPLY))]
    set_gateway_provider(lambda: wire.client())
    try:
        r = client.post(f"/health/documents/{doc.id}/reread", follow_redirects=False)
    finally:
        set_gateway_provider(None)
    assert r.status_code == 303
    session.expire_all()
    doc = session.get(Document, doc.id)
    assert doc.status == DocumentStatus.PROCESSED and doc.category == "lab_result" and doc.failure_reason is None
    assert client.post(f"/health/documents/{doc.id}/reread").status_code == 409     # nothing to redo once read
