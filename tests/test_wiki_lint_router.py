import json

from sqlmodel import select

from app.models.wiki import WikiLink, WikiLogEntry, WikiOperation
from app.models.wiki_lint import LintFinding, LintFindingKind, LintFindingStatus, LintRun
from tests.knowledge_factories import make_page


def _finding(session, kind=LintFindingKind.ORPHAN_PAGE, pages=(1,)):
    run = LintRun(trigger="manual")
    session.add(run)
    session.commit()
    session.refresh(run)
    f = LintFinding(fingerprint=f"fp-{kind.value}-{pages}", kind=kind, summary="Something to check",
                    wiki_page_ids_json=json.dumps(list(pages)), first_seen_run_id=run.id, last_seen_run_id=run.id)
    session.add(f)
    session.commit()
    session.refresh(f)
    return f


def test_lint_page_lists_open_findings_in_plain_language(client, session):
    _finding(session)
    _finding(session, LintFindingKind.STALE_LINK, (1, 2))
    r = client.get("/wiki/lint")
    assert r.status_code == 200 and "Pages not connected to anything" in r.text
    assert "Links that may be out of date" in r.text and "Something to check" in r.text


def test_wiki_index_shows_issue_count_link(client, session):
    _finding(session)
    assert "1 wiki issue" in client.get("/wiki").text


def test_dismiss_and_fixed(client, session):
    f1, f2 = _finding(session), _finding(session, LintFindingKind.CONTRADICTION)
    assert client.post(f"/wiki/lint/findings/{f1.id}/dismiss").status_code == 200
    assert client.post(f"/wiki/lint/findings/{f2.id}/fixed").status_code == 200
    session.expire_all()
    assert session.get(LintFinding, f1.id).status == LintFindingStatus.DISMISSED
    assert session.get(LintFinding, f2.id).status == LintFindingStatus.FIXED


def test_add_link_only_for_missing_link_and_logs_edit(client, session):
    a, b = make_page(session, "Boiler"), make_page(session, "Utility room")
    f = _finding(session, LintFindingKind.MISSING_LINK, (a.id, b.id))
    assert client.post(f"/wiki/lint/findings/{f.id}/add-link").status_code == 200
    link = session.exec(select(WikiLink)).one()
    assert (link.from_page_id, link.to_page_id) == (a.id, b.id)
    assert session.exec(select(WikiLogEntry).where(WikiLogEntry.operation == WikiOperation.EDIT)).first()
    assert client.post(f"/wiki/lint/findings/{_finding(session).id}/add-link").status_code == 400


def test_run_now_starts_background_run(client, monkeypatch):
    calls = []
    async def _fake_execute(session, run_id, client=None):
        calls.append(run_id)
    monkeypatch.setattr("app.routers.wiki_lint.execute_run", _fake_execute)
    r = client.post("/wiki/lint/run", follow_redirects=False)
    assert r.status_code == 303 and calls
