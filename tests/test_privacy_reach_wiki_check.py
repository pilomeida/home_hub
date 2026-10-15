"""The weekly wiki check under privacy levels (decision D16): the AI audit reads only what its level allows,
at the strictest included level, and the run says what it could not read. Captured at the wire with the
real GatewayClient (tests/privacy_world.py). Synthetic data only."""

import json

import pytest
from sqlmodel import select

from app.domains.registry import get_spec
from app.jobs.registry import _run_wiki_lint
from app.llm_gateway import set_gateway_provider
from app.models.domain import Domain
from app.models.schedule import ProblemEntry
from app.models.wiki_lint import LintFinding, LintFindingKind, LintFindingStatus, LintRun, LintRunStatus
from app.privacy import settings as ps
from app.privacy.reach import LeftOut
from app.routers.wiki_lint import get_lint_gateway
from app.services.wiki_lint import llm_checks, runner
from tests import privacy_world as pw


@pytest.fixture()
def world(session, inbox_documents_dir):
    return pw.build_world(session, inbox_documents_dir)


@pytest.fixture()
def gateway_seam():
    yield set_gateway_provider
    set_gateway_provider(None)


def new_run(session) -> LintRun:
    return runner.start_run(session, "manual")


@pytest.mark.asyncio
async def test_tripwire_nothing_from_an_excluded_type_reaches_the_audit(session, world):
    pw.set_levels(session, **pw.STANDARD)
    wire = pw.Wire()

    run = await runner.execute_run(session, new_run(session).id, gateway=wire.client())

    assert run.status == LintRunStatus.SUCCEEDED
    assert len(wire.bodies) == 2                                   # House and Financials; Insurance has nothing readable
    assert pw.leaks(wire.bodies, files=pw.EXCLUDED_FILES) == []
    user = " ".join(b["user"] for b in wire.parsed())
    for hidden in (f"claim {world.claim_mixed.id}:", f"claim {world.claim_high.id}:", f"wiki page {world.page_high.id}:",
                   f"doc {world.c.id} |", f"doc {world.d.id} |", f"doc {world.e.id} |", f"rec {world.rec.id} |"):
        assert hidden not in user, hidden
    for shown in (f"claim {world.claim_clean.id}: room = {pw.CLEAN_ROOM}", f"claim {world.claim_mixed_clean.id}: provider = Aguas",
                  f"doc {world.a.id} |", f"doc {world.b.id} |", f"wiki page {world.page_mixed.id}:"):
        assert shown in user, shown                                # the readable part is audited as before


@pytest.mark.asyncio
async def test_every_audit_call_carries_the_strictest_included_level(session, world):
    pw.set_levels(session, **pw.STANDARD)
    wire = pw.Wire()
    await runner.execute_run(session, new_run(session).id, gateway=wire.client())
    assert [b["sensitivity"] for b in wire.parsed()] == ["house_mid", "house_mid"]

    pw.set_levels(session, low=["house/house_appliance", "financials/bill"])        # only Low types readable
    wire2 = pw.Wire()
    await runner.execute_run(session, new_run(session).id, gateway=wire2.client())
    assert {b["sensitivity"] for b in wire2.parsed()} == {"house_low"}


@pytest.mark.asyncio
async def test_with_levels_off_the_audit_bodies_are_exactly_todays(session, world):
    pw.set_levels(session, on=False, **pw.STANDARD)
    wire = pw.Wire()

    run = await runner.execute_run(session, new_run(session).id, gateway=wire.client())

    assert run.left_out_json is None
    assert len(wire.bodies) == 3 and all("sensitivity" not in b for b in wire.parsed())
    expected = [{"workload_type": "critique_review", "system": llm_checks._SYSTEM_PROMPT, "max_tokens": 16000,
                 "response_schema": llm_checks._FINDINGS_SCHEMA, "user": llm_checks.build_domain_payload(session, spec).text}
                for spec in (get_spec(Domain.FINANCIALS), get_spec(Domain.HOUSE), get_spec(Domain.INSURANCE))]
    assert sorted(wire.parsed(), key=lambda b: b["user"]) == sorted(expected, key=lambda b: b["user"])
    for sentinel in (pw.HIGH, pw.UNSET, pw.MIXED, pw.REC, pw.PAGE):                 # nothing is filtered
        assert any(sentinel in b for b in wire.bodies), sentinel


@pytest.mark.asyncio
async def test_findings_that_name_something_the_audit_was_not_shown_are_dropped(session, world):
    pw.set_levels(session, **pw.STANDARD)
    wire = pw.Wire()
    wire.lint_reply = {"findings": [
        {"kind": "gap", "summary": "Invented.", "wiki_page_ids": [world.page_high.id], "claim_ids": [world.claim_high.id]},
        {"kind": "gap", "summary": "Real.", "wiki_page_ids": [world.page_clean.id, world.page_high.id]}]}
    drafts = await llm_checks.check_domain(session, get_spec(Domain.HOUSE), gateway=wire.client())
    assert [d.summary for d in drafts] == ["Real."] and drafts[0].wiki_page_ids == (world.page_clean.id,)


@pytest.mark.asyncio
async def test_the_checks_that_send_nothing_still_see_everything(session, world):
    pw.set_levels(session, **pw.STANDARD)
    await runner.execute_run(session, new_run(session).id, gateway=pw.Wire().client())
    orphans = session.exec(select(LintFinding).where(LintFinding.kind == LintFindingKind.ORPHAN_PAGE)).all()
    assert any(world.page_high.id in json.loads(f.wiki_page_ids_json) for f in orphans)


@pytest.mark.asyncio
async def test_nothing_readable_means_no_ai_call_at_all_and_the_run_says_so(session, world):
    pw.set_levels(session, high=["insurance/home_insurance"])              # no type is readable
    wire = pw.Wire()
    run = await runner.execute_run(session, new_run(session).id, gateway=wire.client())
    assert wire.bodies == [] and run.status == LintRunStatus.SUCCEEDED
    assert LeftOut.from_json(run.left_out_json).count > 0


@pytest.mark.asyncio
async def test_the_run_summary_and_the_problems_log_say_what_was_left_out_and_clear_when_it_is_not(session, world, gateway_seam):
    pw.set_levels(session, **pw.STANDARD)
    wire = pw.Wire()
    gateway_seam(lambda: wire.client())

    outcome = await _run_wiki_lint(session, "manual")

    left = LeftOut.from_json(session.exec(select(LintRun).order_by(LintRun.id.desc())).first().left_out_json)
    problem = left.problem("check")
    assert (left.documents, left.records) == (4, 1)
    assert problem.what == "5 documents and records weren't available to this check."
    assert outcome.outcome == "partial"                                     # it ran, but not over everything
    assert outcome.summary.startswith("Done:") and problem.text in outcome.summary
    assert "set to High" in problem.why and "no level yet" in problem.why and "hides personal details" in problem.why
    assert problem.next_step == "Change that on the Privacy page, then run the check again."
    logged = session.exec(select(ProblemEntry).where(ProblemEntry.ref == runner.LEFT_OUT_REF)).one()
    assert (logged.area, logged.what, logged.why, logged.next_step, logged.link) == \
        ("Wiki check", problem.what, problem.why, problem.next_step, "/privacy") and logged.resolved_at is None

    again = await _run_wiki_lint(session, "manual")                          # same state: counted, not duplicated
    assert again.outcome == "partial"
    logged = session.exec(select(ProblemEntry).where(ProblemEntry.ref == runner.LEFT_OUT_REF)).one()
    assert logged.times == 2

    for info in ps.known_types():                                           # every type gets a readable level
        ps.save_type(session, info.domain, info.category, "house_low", False, "pedro")
    world.unk.category = "floor_plan"                                       # and the odd document is filed as a real type
    session.add(world.unk)
    session.commit()
    final = await _run_wiki_lint(session, "manual")
    assert final.outcome == "ok" and "weren't available" not in final.summary
    session.refresh(logged)
    assert logged.resolved_at is not None                                   # cleared by the first run that leaves none out
    assert pw.HIGH in "".join(wire.bodies[-3:])                              # and the formerly withheld content is audited now


def test_the_wiki_check_page_shows_the_note_and_runs_the_audit_at_the_level_in_a_background_task(client, session, world):
    pw.set_levels(session, **pw.STANDARD)
    wire = pw.Wire()
    client.app.dependency_overrides[get_lint_gateway] = lambda: wire.client()
    try:
        r = client.post("/wiki/lint/run", follow_redirects=True)
    finally:
        client.app.dependency_overrides.pop(get_lint_gateway, None)
    assert r.status_code == 200
    assert "5 documents and records weren" in r.text and "available to this check" in r.text
    assert "Change that on the Privacy page" in r.text and 'href="/privacy"' in r.text
    assert [b["sensitivity"] for b in wire.parsed()] == ["house_mid", "house_mid"]
    assert pw.leaks(wire.bodies, files=pw.EXCLUDED_FILES) == []

    pw.set_levels(session, on=False)
    wire2 = pw.Wire()
    client.app.dependency_overrides[get_lint_gateway] = lambda: wire2.client()
    try:
        r2 = client.post("/wiki/lint/run", follow_redirects=True)
    finally:
        client.app.dependency_overrides.pop(get_lint_gateway, None)
    assert "available to this check" not in r2.text and all("sensitivity" not in b for b in wire2.parsed())
