"""One copy of each paper, and no voids (work-list B7): a paper still used elsewhere is not taken out of use."""

from datetime import date

import pytest
from sqlmodel import SQLModel, select

import app.models  # noqa: F401  (every table registered)
from app.models.document import Document, DocumentStatus
from app.models.category import Category
from app.models.health import HealthEntry
from app.services.paper_uses import HISTORY_TABLES, USE_TABLES, uses_of
from app.services.todo_engine import upsert_source_todo


def test_every_link_to_a_paper_is_read_by_the_check_or_named_as_history():
    linking = {t.name for t in SQLModel.metadata.sorted_tables
               for fk in t.foreign_keys if fk.target_fullname == "documents.id"}
    missed = linking - USE_TABLES - HISTORY_TABLES
    assert not missed, (f"{missed} link to documents but app/services/paper_uses.py neither reads them as a use "
                        "nor names them as history: a paper could be deleted while they still point at it")
    assert not (USE_TABLES | HISTORY_TABLES) - linking          # no stale names either


def _health_paper(session):
    from tests.test_health_browse import _paper
    return _paper(session, "Ana Lima", date(2026, 1, 1), "test", [], name="d.pdf")


def test_a_health_paper_with_an_open_to_do_is_not_deleted_and_the_page_lists_the_place(client, session):
    doc = _health_paper(session)
    upsert_source_todo(session, doc, title="Book the follow-up", due_date=date(2026, 12, 1), category=Category.HEALTH)

    r = client.post(f"/health/documents/{doc.id}/delete", follow_redirects=False,
                    headers={"Accept": "text/html,application/xhtml+xml"})       # as the browser's form sends it

    assert r.status_code == 409
    assert "Not deleted." in r.text and "one copy of each paper" in r.text and "try again" in r.text
    assert '<a href="/todos">the open to-do “Book the follow-up”</a>' in r.text
    session.expire_all()
    assert session.get(Document, doc.id).status != DocumentStatus.DISCARDED
    assert session.exec(select(HealthEntry).where(HealthEntry.document_id == doc.id)).first() is not None


def test_once_nothing_else_uses_it_the_health_paper_is_deleted(client, session):
    doc = _health_paper(session)
    todo = upsert_source_todo(session, doc, title="Book the follow-up", due_date=date(2026, 12, 1), category=Category.HEALTH)
    todo.done = True
    session.add(todo); session.commit()
    # Its own Health record and wiki facts are what the delete takes back: they never block it.
    assert {u.area for u in uses_of(session, doc)} <= {"health", "wiki"}

    r = client.post(f"/health/documents/{doc.id}/delete", follow_redirects=False)

    assert r.status_code == 303
    session.expire_all()
    assert session.get(Document, doc.id).status == DocumentStatus.DISCARDED


@pytest.mark.usefixtures("two_domains")
def test_a_waiting_paper_already_linked_elsewhere_is_not_discarded_and_the_banner_lists_it(client, session):
    from tests.test_inbox_router import _pending
    doc = _pending(session)
    upsert_source_todo(session, doc, title="Look at the boiler manual", due_date=date(2026, 12, 1), category=Category.HOUSE)

    r = client.post(f"/inbox/{doc.id}/discard", headers={"HX-Request": "true"})

    assert r.status_code == 409 and r.headers["HX-Retarget"] == "#problem-banner"
    assert "Not discarded." in r.text and '<a href="/todos">the open to-do “Look at the boiler manual”</a>' in r.text
    session.expire_all()
    assert session.get(Document, doc.id).status == DocumentStatus.PENDING_REVIEW


def test_a_premium_notice_is_used_on_its_policy_card(client, session, monkeypatch, tmp_path):
    from tests.test_insurance_domain import test_a_premium_notice_uploaded_in_insurance_is_a_bill_shown_on_its_policy as file_both
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "documents")
    file_both(client, session, monkeypatch)
    bill = session.exec(select(Document).where(Document.filename == "aviso.pdf")).one()

    places = [(u.area, u.label, u.link) for u in uses_of(session, bill) if u.area == "insurance"]

    assert len(places) == 1 and places[0][1] == "listed on the MetLife policy PT-123, in Insurance"
    assert places[0][2].startswith("/insurance#policy-")


def test_the_message_reaches_the_page_with_its_places(client, session):
    """The refusal crosses the HTTP boundary as a detail dict: what the person sees is built back from it."""
    from app.problems import Problem, for_http
    problem = Problem("Not deleted.", "Why.", "Next.", places=(("a to-do", "/todos"), ("no link", None)))
    assert for_http(409, problem.as_detail()) == problem


def test_every_path_that_takes_a_paper_out_of_use_runs_the_check_first():
    """Any kind of paper, any area: whatever marks a paper discarded must ask refuse_if_used in the same function."""
    import ast
    from pathlib import Path
    unguarded = []
    for path in Path("app").rglob("*.py"):
        for fn in ast.walk(ast.parse(path.read_text())):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            src = ast.unparse(fn)
            if "DocumentStatus.DISCARDED" in src and "refuse_if_used" not in src and (
                    " = DocumentStatus.DISCARDED" in src or "status=DocumentStatus.DISCARDED" in src):
                unguarded.append(f"{path}:{fn.lineno} {fn.name}")
    assert not unguarded, f"these take a paper out of use without the check (services/paper_uses.py): {unguarded}"
