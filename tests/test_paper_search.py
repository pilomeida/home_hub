"""PLAN step 1a-b: every filed paper has a text layer, and search finds a paper by a word printed inside it, from the
search code and from Ask's find_sources, never returning what the AI may not read."""

import pytest
from sqlmodel import select

from app.llm_gateway import GatewayError
from app.models.category import Category
from app.models.document import DocumentSource
from app.models.paper_text import AI_READ, FAILED, NO_TEXT, PDF_TEXT, PaperText
from app.models.schedule import ProblemEntry
from app.services import ingestion, paper_search as ps_
from app.services.ask import tools as ask_tools
from app.services.ingestion import Classification, IncomingFile
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.fakes.text_pdf import text_pdf_bytes
from tests.knowledge_factories import make_document

BODY = ["Contrato de prestacao de servicos de jardinagem", "Clausula terceira: o prestador Joao Caldeira",
        "compromete-se a podar as oliveiras em Mafra", "Pagamento mensal de 45,00 EUR por transferencia"]
PNG = b"\x89PNG\r\n\x1a\nphoto-bytes"


def _incoming(name="contract.pdf", content=None):
    return IncomingFile(filename=name, content=content if content is not None else text_pdf_bytes(BODY),
                        source=DocumentSource.MANUAL, uploaded_by="pedro@example.com")


async def _file(session, incoming):
    return (await ingestion.ingest(session, incoming, Classification(Category.HOUSE, "manual", {"item_name": "Garden"}))).document


@pytest.mark.asyncio
async def test_a_filed_pdf_keeps_its_own_text_and_is_found_by_a_word_inside_it(session, inbox_documents_dir, fake_category):
    doc = await _file(session, _incoming())
    row = session.exec(select(PaperText).where(PaperText.document_id == doc.id)).one()
    assert row.method == PDF_TEXT and "oliveiras" in row.text
    hits = ps_.search_papers(session, "oliveiras")
    assert [h.document.id for h in hits] == [doc.id]
    assert "oliveiras" in hits[0].snippet_plain() and "\x02" in hits[0].snippet     # the line that matched, marked


@pytest.mark.asyncio
async def test_search_folds_accents_and_matches_the_start_of_a_word(session, inbox_documents_dir, fake_category):
    doc = await _file(session, _incoming())
    assert [h.document.id for h in ps_.search_papers(session, "Caldéira")] == [doc.id]   # printed without the accent
    assert [h.document.id for h in ps_.search_papers(session, "jardin")] == [doc.id]     # start of "jardinagem"
    assert ps_.search_papers(session, "piscina") == []


@pytest.mark.asyncio
async def test_more_words_found_ranks_first(session, inbox_documents_dir, fake_category):
    one = await _file(session, _incoming("a.pdf", text_pdf_bytes(["Oliveiras e laranjeiras do quintal"] * 3)))
    both = await _file(session, _incoming())
    assert [h.document.id for h in ps_.search_papers(session, "oliveiras mafra")] == [both.id, one.id]


@pytest.mark.asyncio
async def test_find_sources_finds_a_paper_by_a_word_in_its_text_and_shows_the_line(session, inbox_documents_dir, fake_category):
    doc = await _file(session, _incoming())
    out = next(t for t in ask_tools.core_tools() if t.name == "find_sources").run(session, {"query": "oliveiras"})
    assert f"doc:{doc.id}" in out.text and "oliveiras" in out.text and "text: …" in out.text
    assert out.text.startswith("1 matching")


@pytest.mark.asyncio
async def test_editing_a_papers_details_updates_its_search_row(session, inbox_documents_dir, fake_category):
    doc = await _file(session, _incoming())
    await ingestion.update_document_fields(session, doc, {"item_name": "Pomar"})
    assert [h.document.id for h in ps_.search_papers(session, "pomar")] == [doc.id]
    assert ps_.search_papers(session, "garden") == []


def test_a_paper_not_filed_is_never_returned(session, fake_category):
    from app.models.document import DocumentStatus
    pending = make_document(session, category=None, paper_kind=None, status=DocumentStatus.PENDING_REVIEW)
    session.add(PaperText(document_id=pending.id, text="segredo", method=PDF_TEXT))
    session.commit()
    ps_.rebuild_index(session)
    assert ps_.search_papers(session, "segredo") == []


def test_a_scan_has_no_text_layer(tmp_path):
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(text_pdf_bytes(["p. 1"]))
    assert ps_.pdf_text_of(str(scan)) is None
    png = tmp_path / "x.png"
    png.write_bytes(PNG)
    assert ps_.pdf_text_of(str(png)) is None


@pytest.mark.asyncio
async def test_the_job_has_the_ai_read_a_photo_at_its_level_and_marks_a_video(session, inbox_documents_dir, fake_category, levels_not_binding):
    inbox_documents_dir.mkdir(parents=True, exist_ok=True)
    photo_path = inbox_documents_dir / "receipt.png"
    photo_path.write_bytes(PNG)
    video_path = inbox_documents_dir / "clip.mp4"
    video_path.write_bytes(b"video")
    photo = make_document(session, filename="receipt.png", file_path=str(photo_path))
    video = make_document(session, filename="clip.mp4", file_path=str(video_path), paper_kind="clip")
    fake = FakeGateway([gateway_text_result("Recibo Leroy Merlin - tinta branca 2 latas")])
    run = await ps_.read_missing_texts(session, gateway=fake)
    assert (run.read, run.no_text, run.failed) == (1, 1, 0)
    assert len(fake.requests) == 1 and fake.requests[0]["attachments"][0]["type"] == "image"
    rows = {r.document_id: r for r in session.exec(select(PaperText))}
    assert rows[photo.id].method == AI_READ and rows[video.id].method == NO_TEXT
    assert [h.document.id for h in ps_.search_papers(session, "leroy")] == [photo.id]
    again = await ps_.read_missing_texts(session, gateway=FakeGateway())
    assert (again.read, again.no_text) == (0, 0)                       # nothing read twice


@pytest.mark.asyncio
async def test_a_health_paper_is_read_only_at_high(session, inbox_documents_dir, levels_not_binding):
    inbox_documents_dir.mkdir(parents=True, exist_ok=True)
    path = inbox_documents_dir / "lab.png"
    path.write_bytes(PNG)
    make_document(session, category=Category.HEALTH, paper_kind="lab_result", filename="lab.png", file_path=str(path))
    fake = FakeGateway([gateway_text_result("Hemograma")])
    await ps_.read_missing_texts(session, gateway=fake)
    assert fake.requests[0]["sensitivity"] == "house_high"


@pytest.mark.asyncio
async def test_a_failed_read_is_tried_three_times_then_shown_on_problems(session, inbox_documents_dir, fake_category, levels_not_binding):
    inbox_documents_dir.mkdir(parents=True, exist_ok=True)
    path = inbox_documents_dir / "receipt.png"
    path.write_bytes(PNG)
    doc = make_document(session, filename="receipt.png", file_path=str(path))
    for _ in range(4):
        await ps_.read_missing_texts(session, gateway=FakeGateway(errors=[GatewayError(503, "overloaded")]))
    row = session.exec(select(PaperText).where(PaperText.document_id == doc.id)).one()
    assert row.method == FAILED and row.attempts == 3                    # the fourth run left it alone
    problem = session.exec(select(ProblemEntry).where(ProblemEntry.ref == f"document:{doc.id}:text")).one()
    assert "receipt.png" in problem.what and problem.why and problem.next_step


@pytest.mark.asyncio
async def test_the_ai_read_waits_while_its_kind_has_no_level(session, inbox_documents_dir):
    from tests import privacy_world as pw
    inbox_documents_dir.mkdir(parents=True, exist_ok=True)
    path = inbox_documents_dir / "receipt.png"
    path.write_bytes(PNG)
    make_document(session, category=Category.HOUSE, paper_kind="house_appliance", filename="receipt.png", file_path=str(path))
    pw.set_levels(session, on=True)                                       # binding, and this kind has no level
    fake = FakeGateway()
    run = await ps_.read_missing_texts(session, gateway=fake)
    assert run.waiting_for_level == 1 and fake.requests == []
    assert session.exec(select(PaperText)).all() == []


@pytest.mark.asyncio
async def test_the_job_reports_in_plain_words(session, inbox_documents_dir, fake_category):
    from app.jobs.registry import JOBS
    await _file(session, _incoming())
    outcome = await JOBS["paper_text"].run(session, "manual")
    assert outcome.outcome == "ok" and "1 paper(s) searchable" in outcome.summary


# --- privacy tripwires (decision D16): search returns only what the AI may read --------------------------------

def test_search_with_binding_levels_returns_only_reachable_papers(session, inbox_documents_dir):
    from app.privacy.reach import compute_reach
    from tests import privacy_world as pw
    w = pw.build_world(session, inbox_documents_dir)
    pw.set_levels(session, **pw.STANDARD)
    reach = compute_reach(session)
    found = {h.document.id for h in ps_.search_papers(session, "bodytext", reach=reach)}
    assert w.a.id in found and w.c.id in found and w.health_paper.id in found        # included (c and Health private)
    assert not found & {w.d.id, w.e.id, w.unk.id}                                    # no level / hides / unknown type
    first_call = {h.document.id for h in ps_.search_papers(session, "bodytext", reach=reach.without_private())}
    assert first_call == {w.a.id}                                                     # private never in the first view


@pytest.mark.asyncio
async def test_ask_reads_paper_text_but_never_excluded_text_and_private_text_only_at_high(session, inbox_documents_dir):
    import httpx  # noqa: F401
    from app.services.ask.conversation import start_conversation
    from app.services.ask.engine import run_turn
    from datetime import date
    from tests import privacy_world as pw
    w = pw.build_world(session, inbox_documents_dir)
    pw.set_levels(session, **pw.STANDARD)
    wire = pw.Wire(script=pw.ask_script(w))
    turn = start_conversation(session, "What do we have?", started_by=None)
    await run_turn(session, turn.id, gateway=wire.client(), today=date(2026, 10, 15))
    bodies = wire.bodies
    assert any(pw.CLEAN_TEXT in b for b in bodies)                                    # search reached the text layer
    for printed in (f"{pw.UNSET}-printed", f"{pw.HIDES}-printed", f"{pw.UNKNOWN}-printed"):
        assert not any(printed in b for b in bodies), printed
    for printed in (f"{pw.HIGH}-printed", f"{pw.HEALTH_PAPER}-printed"):
        for body in wire.parsed():
            if body.get("sensitivity") != "house_high":
                assert printed not in str(body), printed


# --- d. the search page ------------------------------------------------------------------------------------

def test_the_search_box_is_on_every_page_and_results_show_paper_level_and_line(client, engine, inbox_documents_dir):
    from sqlmodel import Session
    from app.privacy import settings as privacy_settings
    with Session(engine) as session:
        doc = make_document(session, category=Category.INSURANCE, paper_kind="policy", filename="zurich-lar.pdf",
                            fields={"insurer": "Zurich", "policy_number": "P1", "covers": "home", "part": "other"})
        privacy_settings.save_type(session, "insurance", "policy", "house_mid", False, "test")
        session.add(PaperText(document_id=doc.id, text="Cobertura de <b>fenómenos</b> sísmicos até 50.000 EUR",
                              method=PDF_TEXT))
        session.commit()
        ps_.rebuild_index(session)
        doc_id = doc.id
    assert 'action="/search"' in client.get("/").text and 'action="/search"' in client.get("/papers").text
    page = client.get("/search", params={"q": "sismicos"}).text
    assert f'href="/insurance/documents/{doc_id}"' in page and "Insurance › Policy" in page and "<td>Mid</td>" in page
    assert "<mark>sísmicos</mark>" in page
    assert "&lt;b&gt;" in page and "<b>fenómenos</b>" not in page                       # the paper's text is escaped
    assert "No filed paper has these words" in client.get("/search", params={"q": "piscina"}).text
    assert "Nothing searched" in client.get("/search", params={"q": "a"}).text
