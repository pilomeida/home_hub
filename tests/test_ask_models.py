import json

from app.models.ask import AskConversation, AskStatus, AskTurn
from app.models.wiki_lint import LintFinding, LintFindingKind, LintFindingStatus, LintRun, LintRunStatus


def test_conversation_and_turn_defaults(session):
    conv = AskConversation(title="Boiler", started_by="pedro@example.com")
    session.add(conv)
    session.commit()
    session.refresh(conv)
    turn = AskTurn(conversation_id=conv.id, position=1, question="When was the boiler serviced?")
    session.add(turn)
    session.commit()
    session.refresh(turn)
    assert turn.status == AskStatus.PENDING and json.loads(turn.citations_json) == []
    assert turn.used_raw_sources is False and turn.saved_wiki_page_id is None


def test_lint_run_and_finding_roundtrip(session):
    run = LintRun(trigger="manual")
    session.add(run)
    session.commit()
    session.refresh(run)
    assert run.status == LintRunStatus.RUNNING
    finding = LintFinding(fingerprint="abc", kind=LintFindingKind.STALE_LINK, summary="x",
                          first_seen_run_id=run.id, last_seen_run_id=run.id)
    session.add(finding)
    session.commit()
    session.refresh(finding)
    assert finding.status == LintFindingStatus.OPEN and json.loads(finding.wiki_page_ids_json) == []
