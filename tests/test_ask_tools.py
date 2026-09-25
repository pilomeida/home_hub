import dataclasses
from datetime import datetime

import pytest

from app.domains import registry
from app.models.document import DocumentStatus
from app.models.domain import Domain
from app.models.todo import Todo
from app.services.ask import tools as ask_tools
from app.services.ask.contracts import AskTool, ToolOutput
from tests.domain_fakes import make_fake_spec
from tests.knowledge_factories import make_claim, make_document, make_link, make_page, make_record


def _tool(name):
    return next(t for t in ask_tools.core_tools() if t.name == name)


def test_settled_entries_are_processed_documents_and_live_records(session, fake_domain):
    ok = make_document(session, filename="manual.pdf")
    make_document(session, filename="inbox.pdf", domain=None, category=None, status=DocumentStatus.PENDING_REVIEW)
    make_document(session, filename="broken.pdf", status=DocumentStatus.NEEDS_ATTENTION)
    visit = make_record(session, fields={"item_name": "Boiler", "visit_date": "2026-03-02"})
    make_record(session, fields={"item_name": "Old"}, retired=True)
    entries = ask_tools.settled_entries(session)
    assert {(e.kind.value, (e.record or e.document).id) for e in entries} == {("document", ok.id), ("record", visit.id)}


def test_read_wiki_pages_notes_sources_withdrawn_links_and_neighbours(session, fake_domain):
    doc = make_document(session, filename="warranty.pdf")
    old_doc = make_document(session, filename="old.pdf")
    visit = make_record(session, fields={"item_name": "Boiler", "visit_date": "2026-03-02"})
    boiler = make_page(session, "Boiler", summary="Vaillant boiler")
    kitchen = make_page(session, "Kitchen")
    make_claim(session, boiler, "visited", "2025-01-10", superseded_at=datetime(2026, 3, 2))
    make_claim(session, boiler, "visited", "2026-03-02", record_ids=[visit.id])
    make_claim(session, boiler, "warranty_expires", "2029-03-12", doc_ids=[doc.id], withdrawn_doc_ids=[old_doc.id],
               note="Assumed: Portugal's 3-year legal guarantee from the purchase date (12 Mar 2026)")
    make_link(session, boiler, kitchen)

    out = _tool("read_wiki_pages").run(session, {"page_ids": [boiler.id]})

    assert f"rec:{visit.id}" in out.text and f"doc:{doc.id}" in out.text
    assert f"doc:{old_doc.id}" not in out.text                      # withdrawn link: not a source
    assert "NOTE: Assumed: Portugal's 3-year legal guarantee" in out.text
    assert "Superseded" in out.text and "2025-01-10" in out.text and f"wiki:{kitchen.id} Kitchen" in out.text
    refs = {c.ref for c in out.citables}
    assert {f"wiki:{boiler.id}", f"doc:{doc.id}", f"rec:{visit.id}", f"wiki:{kitchen.id}"} <= refs
    assert f"doc:{old_doc.id}" not in refs
    assert next(c for c in out.citables if c.ref == f"rec:{visit.id}").url == f"/fake/records/{visit.id}"


def test_find_sources_searches_documents_and_records_accent_insensitive(session, fake_domain):
    hit = make_document(session, filename="caldeira.pdf", fields={"item_name": "Caldeira Vaillant"})
    visit = make_record(session, fields={"item_name": "Caldeira Vaillant", "visit_date": "2026-03-02"})
    make_document(session, filename="lawnmower.pdf", fields={"item_name": "Lawnmower"})
    make_document(session, filename="pending.pdf", domain=None, status=DocumentStatus.PENDING_REVIEW)

    out = _tool("find_sources").run(session, {"query": "caldéira"})

    assert f"doc:{hit.id}" in out.text and f"rec:{visit.id}" in out.text and "Visit date: 2026-03-02" in out.text
    assert "lawnmower" not in out.text and "pending" not in out.text
    assert {c.ref for c in out.citables} == {f"doc:{hit.id}", f"rec:{visit.id}"}
    assert out.used_raw_sources is True


def test_find_sources_shows_a_records_attachment_for_reading(session, fake_domain):
    pdf = make_document(session, filename="invoice.pdf", category="visit")
    visit = make_record(session, fields={"item_name": "Boiler", "visit_date": "2026-03-02"}, document_id=pdf.id)
    out = _tool("find_sources").run(session, {"query": "boiler"})
    assert f"rec:{visit.id}" in out.text and f"attachment doc:{pdf.id}" in out.text


def test_read_document_file_refuses_unsettled_and_attaches_settled(session, fake_domain, tmp_path):
    pdf = tmp_path / "w.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    good = make_document(session, filename="w.pdf", file_path=str(pdf))
    pending = make_document(session, filename="p.pdf", file_path=str(pdf), domain=None,
                            status=DocumentStatus.PENDING_REVIEW)
    refused = _tool("read_document_file").run(session, {"document_id": pending.id})
    assert refused.is_error and refused.content_blocks == []
    out = _tool("read_document_file").run(session, {"document_id": good.id})
    assert out.content_blocks[0]["type"] == "document" and out.used_raw_sources is True


def test_read_document_file_video_is_a_soft_error(session, fake_domain, tmp_path):
    vid = tmp_path / "pipes.mp4"
    vid.write_bytes(b"0000")
    doc = make_document(session, filename="pipes.mp4", file_path=str(vid), category="clip")
    out = _tool("read_document_file").run(session, {"document_id": doc.id})
    assert out.is_error and "details" in out.text


def test_list_todos_open_only_and_links_to_domain_backlog(session, fake_domain):
    session.add(Todo(title="Renew boiler warranty", domain=Domain.HOUSE))
    session.add(Todo(title="Old", done=True, domain=Domain.HOUSE))
    session.commit()
    out = _tool("list_todos").run(session, {})
    assert "Renew boiler warranty" in out.text and "Old" not in out.text
    assert out.citables[0].url.startswith("/fake#todo-")


def test_domain_tools_must_be_prefixed(monkeypatch):
    bad = AskTool(name="spending", description="d", input_schema={"type": "object"},
                  run=lambda s, a: ToolOutput(text=""))
    spec = dataclasses.replace(make_fake_spec(), ask_tools=(bad,))
    monkeypatch.setattr(registry, "_specs_cache", {spec.domain: spec})
    with pytest.raises(ValueError):
        ask_tools.domain_tools()
