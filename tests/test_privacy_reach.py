"""Ask under privacy levels (decision D16): what the AI may read, at which level, and what the person is
told. Everything is captured at the wire with the real GatewayClient (see tests/privacy_world.py).
Synthetic data only."""

import base64
from datetime import date, datetime

import httpx
import pytest
from sqlmodel import select

from app.llm_gateway import GatewayClient
from app.models.ask import AskStatus
from app.models.domain import Domain
from app.models.schedule import ProblemEntry
from app.models.wiki import WikiClaimSource
from app.privacy import settings as ps
from app.privacy.reach import HIDES as R_HIDES, HIGH as R_HIGH, UNSET as R_UNSET, LeftOut, LeftOutType, compute_reach
from app.privacy.scope import current_scope, reachable_scope
from app.privacy.tiers import Tier
from app.routers.ask import get_ask_gateway
from app.services.ask import messages as ask_messages
from app.services.ask.context import build_system_prompt
from app.services.ask.conversation import add_turn, start_conversation
from app.services.ask.engine import run_turn
from app.services.ask.tools import available_tools, settled_entries
from tests import privacy_world as pw
from tests.fakes.fake_gateway import gateway_text_result
from tests.knowledge_factories import make_conversation, make_document, make_record, make_turn

TODAY = date(2026, 10, 15)


@pytest.fixture()
def world(session, inbox_documents_dir):
    return pw.build_world(session, inbox_documents_dir)


async def ask(session, wire, w, question="What do we have?"):
    wire.script = pw.ask_script(w)
    turn = start_conversation(session, question, started_by=None)
    return await run_turn(session, turn.id, gateway=wire.client(), today=TODAY)


# --- the rules: which types are reachable, and at which level --------------------------------------

def test_a_type_is_excluded_if_high_or_unset_or_hides_personal_details(session):
    pw.set_levels(session, low=["house/house_appliance"], mid=["financials/bill"],
                  high=["insurance/home_insurance"], hides=["house/ownership_document"])
    reach = compute_reach(session)
    got = {k: (a.included, a.reason) for k, a in reach.access.items()}
    assert got[("house", "house_appliance")] == (True, None)
    assert got[("financials", "bill")] == (True, None)
    assert got[("insurance", "home_insurance")] == (False, R_HIGH)
    assert got[("house", "ownership_document")] == (False, R_HIDES)         # Mid, but hiding cannot work with look-ups
    assert got[("financials", "statement")] == (False, R_UNSET)
    assert reach.includes_type("house", "no_such_type") is False            # not a registered type: no level
    assert reach.includes_type(None, None) is False


@pytest.mark.parametrize("levels,expected", [
    (dict(low=["house/house_appliance"]), Tier.LOW),
    (dict(low=["house/house_appliance"], mid=["financials/bill"]), Tier.MID),      # strictest included
    (dict(mid=["financials/bill"], high=["insurance/home_insurance"]), Tier.MID),  # High is excluded, not "strictest"
    (dict(high=["insurance/home_insurance"]), Tier.MID),                           # nothing included: Mid floor
    (dict(), Tier.MID),
])
def test_the_call_level_is_the_strictest_included_level(session, levels, expected):
    pw.set_levels(session, **levels)
    assert compute_reach(session).tier == expected


def test_when_levels_are_not_binding_nothing_is_excluded_and_no_scope_is_set(session):
    pw.set_levels(session, on=False, high=["insurance/home_insurance"])
    reach = compute_reach(session)
    assert reach.binding is False and reach.tier is None and reach.includes_type("insurance", "home_insurance")
    with reachable_scope(session) as r:
        assert r.binding is False and current_scope() is None              # the gateway adds no sensitivity


def test_the_scope_carries_the_level_and_no_redaction_and_is_reset(session):
    pw.set_levels(session, mid=["financials/bill"])
    with reachable_scope(session):
        scope = current_scope()
        assert scope.tier == Tier.MID and scope.redaction is None
    assert current_scope() is None


# --- Ask over the wire --------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tripwire_nothing_from_an_excluded_type_leaves_the_server_in_a_full_ask_turn(session, world):
    pw.set_levels(session, **pw.STANDARD)
    wire = pw.Wire()

    turn = await ask(session, wire, world)

    assert turn.status == AskStatus.ANSWERED and len(wire.bodies) == 2
    # The turn uses EVERY tool Ask has (a new tool, core or domain, fails here until the tripwire covers it).
    called = {b["name"] for b in wire.parsed()[1]["messages"][1]["content"]}
    assert called == {t.name for t in available_tools()}
    assert pw.leaks(wire.bodies, files=pw.EXCLUDED_FILES) == []
    # Positive controls: the included types DID reach the AI, so the tripwire is not vacuous.
    last = wire.bodies[1]
    for clean in (pw.CLEAN_ROOM, pw.CLEAN_PAYEE, pw.CLEAN_TODO, pw.CLEAN_ANSWER, "boiler-manual.png", "Aguas"):
        assert clean in last, clean
    assert base64.b64encode(pw.PNG).decode() in last                        # the included file was readable


@pytest.mark.asyncio
async def test_with_levels_on_every_call_carries_the_strictest_included_level(session, world):
    pw.set_levels(session, **pw.STANDARD)                                   # included: Low (appliance) and Mid (bill)
    wire = pw.Wire()
    await ask(session, wire, world)
    assert [b["sensitivity"] for b in wire.parsed()] == ["house_mid", "house_mid"]


@pytest.mark.asyncio
async def test_only_low_types_included_means_a_low_call(session, world):
    pw.set_levels(session, low=["house/house_appliance", "financials/bill"])
    wire = pw.Wire()
    await ask(session, wire, world)
    assert {b["sensitivity"] for b in wire.parsed()} == {"house_low"}


@pytest.mark.asyncio
async def test_with_levels_off_the_outbound_body_is_exactly_todays(session, world):
    pw.set_levels(session, on=False, **pw.STANDARD)                         # levels saved, not binding
    wire = pw.Wire()
    await ask(session, wire, world, "Anything?")

    first = wire.parsed()[0]
    assert all("sensitivity" not in b for b in wire.parsed())
    expected_system = build_system_prompt(session, TODAY)[0]                # outside any scope, levels off
    assert first == {"workload_type": "interactive_research", "system": expected_system, "max_tokens": 16000,
                     "response_schema": None, "job_id": first["job_id"],
                     "messages": [{"role": "user", "content": "Anything?"}],
                     "tools": [t.to_api() for t in available_tools()]}
    assert "withheld" not in expected_system                                # no privacy note either
    # and nothing is filtered: the sentinels the binding run withheld are all there
    for sentinel in (pw.HIGH, pw.UNSET, pw.HIDES, pw.REC, pw.UNKNOWN, pw.PAGE, pw.MIXED, pw.SUMMARY, pw.HISTORY,
                     pw.TXN, pw.TODO, pw.NEIGHBOUR):
        assert any(sentinel in b for b in wire.bodies), sentinel


@pytest.mark.asyncio
async def test_the_system_prompt_counts_wiki_index_and_pending_note_cover_only_what_may_be_read(session, world):
    pw.set_levels(session, **pw.STANDARD)
    wire = pw.Wire()
    await ask(session, wire, world)
    system = wire.parsed()[0]["system"]
    domains = system.split("Wiki index:")[0]
    assert "withheld from you" in system                                    # the model is told the records are incomplete
    assert "[bill: 1]" in domains and "[house_appliance: 1]" in domains
    for hidden in ("statement:", "home_insurance:", "ownership_document:", "maintenance_log:", "legacy_kind:"):
        assert hidden not in domains, hidden
    assert f"wiki:{world.page_clean.id} Boiler" in system and f"wiki:{world.page_mixed.id} Water supply" in system
    assert f"wiki:{world.page_high.id}" not in system and f"wiki:{world.page_neighbour.id}" not in system
    assert pw.SUMMARY not in system and "Aguas" in system                   # summary rebuilt from the visible claims
    assert "1 document(s) are awaiting review" in system                    # not 2: one is suggested as a High type
    assert "pending-" not in system


@pytest.mark.asyncio
async def test_a_hidden_page_looks_like_a_missing_one_and_hidden_neighbours_are_not_listed(session, world):
    pw.set_levels(session, **pw.STANDARD)
    wire = pw.Wire()
    await ask(session, wire, world)
    tool_results = wire.parsed()[1]["messages"][-1]["content"]
    wiki = next(r for r in tool_results if r["tool_use_id"] == "t1")["content"]
    assert f"wiki:{world.page_high.id}: not found" in wiki and "wiki:9999: not found" in wiki
    assert "Linked pages" not in wiki                                       # the only neighbour is hidden
    assert "Aguas" in wiki and pw.MIXED not in wiki and pw.HISTORY not in wiki
    unreadable = next(r for r in tool_results if r["tool_use_id"] == "t4")
    missing = "That document is not available (not found, or not filed yet)."
    assert unreadable["is_error"] and unreadable["content"] == missing      # a withheld file reads like a missing one


def test_a_claim_with_any_excluded_source_is_hidden_even_if_another_source_is_included(session, world):
    pw.set_levels(session, **pw.STANDARD)
    view = compute_reach(session).wiki_view(session)
    assert world.claim_mixed.id in view.hidden_claims                       # sources: bill (included) AND insurance (High)
    assert world.claim_mixed_clean.id not in view.hidden_claims
    assert world.claim_history.id in view.hidden_claims                     # superseded history counts too
    assert world.page_high.id in view.hidden_pages and world.page_neighbour.id in view.hidden_pages
    assert world.page_mixed.id in view.trimmed_pages and world.page_mixed.id not in view.hidden_pages
    assert world.page_answer.id not in view.hidden_pages and world.page_clean.id not in view.hidden_pages  # no excluded source
    assert world.claim_clean.id not in view.hidden_claims


def test_a_withdrawn_link_to_an_excluded_document_no_longer_hides_a_claim(session, world):
    pw.set_levels(session, **pw.STANDARD)
    link = session.exec(select(WikiClaimSource).where(WikiClaimSource.claim_id == world.claim_mixed.id,
                                                      WikiClaimSource.document_id == world.c.id)).one()
    link.withdrawn_at = datetime(2026, 5, 1)
    session.add(link)
    session.commit()
    assert world.claim_mixed.id not in compute_reach(session).wiki_view(session).hidden_claims


@pytest.mark.asyncio
async def test_a_type_set_later_brings_its_content_back(session, world):
    pw.set_levels(session, **pw.STANDARD)
    wire = pw.Wire()
    await ask(session, wire, world)
    assert pw.UNSET not in "".join(wire.bodies)

    ps.save_type(session, "financials", "statement", "house_mid", False, "pedro")      # the unset type gets a level
    wire2 = pw.Wire()
    turn = await ask(session, wire2, world)
    assert any(f"{pw.UNSET}-statement.pdf" in b for b in wire2.bodies)                 # its document is listed now
    assert any(f"{pw.TXN}-payee" in b for b in wire2.bodies)                           # and its transactions
    assert any(f"{pw.HISTORY}-old" in b for b in wire2.bodies)                         # and the history it supported
    assert pw.HIGH not in "".join(wire2.bodies)                                        # High stays out
    left = LeftOut.from_json(turn.left_out_json)
    assert "financials/statement" not in left.excluded and "Bank statement" not in [t.label for t in left.types]

    ps.save_type(session, "financials", "statement", "", False, "pedro")               # unset again: gone again
    wire3 = pw.Wire()
    await ask(session, wire3, world)
    assert pw.UNSET not in "".join(wire3.bodies) and pw.TXN not in "".join(wire3.bodies)


def test_a_record_follows_its_own_type_and_its_attached_file_is_checked_separately(session, inbox_documents_dir):
    attachment = make_document(session, domain=Domain.HOUSE, category="ownership_document",
                               filename=f"{pw.HIDES}-receipt.pdf", file_path=str(inbox_documents_dir / "r.pdf"))
    record = make_record(session, domain=Domain.HOUSE, category="maintenance_log", document_id=attachment.id,
                         fields={"item_name": "Boiler", "notes": "serviced"})
    pw.set_levels(session, low=["house/maintenance_log"], hides=["house/ownership_document"])
    reach = compute_reach(session)
    entries = settled_entries(session, reach=reach)
    assert [e.record.id for e in entries] == [record.id] and entries[0].document is None   # record stays, file withheld
    assert pw.HIDES not in entries[0].title
    assert not reach.includes_file_of(session, attachment)

    ps.save_type(session, "house", "maintenance_log", "house_high", False, "pedro")
    reach = compute_reach(session)
    assert settled_entries(session, reach=reach) == []
    assert reach.left_out(session).records == 1

    # A file of a readable type that is attached to a record of a left-out type is not readable either.
    receipt = make_document(session, domain=Domain.HOUSE, category="house_appliance", filename="receipt.pdf",
                            file_path=str(inbox_documents_dir / "receipt.pdf"))
    make_record(session, domain=Domain.HOUSE, category="maintenance_log", document_id=receipt.id)
    ps.save_type(session, "house", "house_appliance", "house_low", False, "pedro")
    reach = compute_reach(session)
    assert reach.includes_document(receipt) and not reach.includes_file_of(session, receipt)


# --- what the person is told ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_each_answer_says_how_many_documents_were_left_out_why_and_what_to_do(session, world):
    pw.set_levels(session, **pw.STANDARD)
    turn = await ask(session, pw.Wire(), world)

    left = LeftOut.from_json(turn.left_out_json)
    problem = left.problem("answer")
    # settled and excluded: statement (no level), policy (High), deed (hides), unknown kind (no level), 1 High record
    assert (left.documents, left.records) == (4, 1)
    assert problem.what == "5 documents and records weren't available to this answer."
    for part in ("Home insurance", "Maintenance log", "set to High", "Bank statement", "no level yet",
                 "Ownership document hides personal details", "Nothing from them was sent to the AI"):
        assert part in problem.why, part
    assert problem.next_step == "Change that on the Privacy page, then ask again." and problem.link == "/privacy"
    assert ask_messages.notices(turn)[0] == problem


def test_the_notice_wording_is_singular_and_plural_correctly():
    one = LeftOut(True, ("a/b",), (LeftOutType("Bill", R_UNSET, 1, 0),)).problem("answer")
    assert one.what == "1 document wasn't available to this answer." and "Bill has no level yet" in one.why
    assert LeftOut(True, ("a/b",), ()).problem("answer") is None            # nothing left out: no note
    assert LeftOut().problem("answer") is None                              # levels not binding: no note
    check = LeftOut(True, (), (LeftOutType("Bill", R_HIGH, 2, 0),)).problem("check")
    assert check.what == "2 documents weren't available to this check." and check.next_step.endswith("run the check again.")


def test_the_conversation_page_shows_the_note_and_the_background_task_carries_the_level(client, session, world):
    pw.set_levels(session, **pw.STANDARD)
    wire = pw.Wire()
    wire.script = pw.ask_script(world)
    client.app.dependency_overrides[get_ask_gateway] = lambda: wire.client()
    try:
        r = client.post("/ask", data={"question": "What do we have?"}, follow_redirects=True)   # answered in a background task
    finally:
        client.app.dependency_overrides.pop(get_ask_gateway, None)
    assert r.status_code == 200
    assert "5 documents and records weren" in r.text and "available to this answer" in r.text
    assert "Home insurance" in r.text and 'href="/privacy"' in r.text
    assert [b["sensitivity"] for b in wire.parsed()] == ["house_mid", "house_mid"]
    assert pw.leaks(wire.bodies, files=pw.EXCLUDED_FILES) == []

    pw.set_levels(session, on=False)
    wire2 = pw.Wire()
    wire2.script = pw.ask_script(world)
    client.app.dependency_overrides[get_ask_gateway] = lambda: wire2.client()
    try:
        r2 = client.post("/ask", data={"question": "Again?"}, follow_redirects=True)
    finally:
        client.app.dependency_overrides.pop(get_ask_gateway, None)
    assert "available to this answer" not in r2.text and all("sensitivity" not in b for b in wire2.parsed())


# --- earlier answers in the same chat ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_answer_written_before_a_type_was_withheld_is_not_replayed(session, world):
    secret = "EARLIER-ANSWER-SENTINEL"
    pw.set_levels(session, on=False, **pw.STANDARD)                          # the first answer is given with levels off
    first = start_conversation(session, "First question?", started_by=None)
    wire = pw.Wire()
    wire.script = [gateway_text_result(f"The policy number is {secret} [[doc:{world.c.id}]].")]
    await run_turn(session, first.id, gateway=wire.client(), today=TODAY)

    pw.set_levels(session, **pw.STANDARD)                                    # now levels bind
    follow = add_turn(session, first.conversation_id, "And the premium?", asked_by=None)
    wire2 = pw.Wire()
    wire2.script = [gateway_text_result("I can't tell.")]
    turn = await run_turn(session, follow.id, gateway=wire2.client(), today=TODAY)
    assert secret not in "".join(wire2.bodies) and f"[[doc:{world.c.id}]]" not in "".join(wire2.bodies)
    assert wire2.parsed()[0]["messages"] == [{"role": "user", "content": "And the premium?"}]
    notes = ask_messages.notices(turn)
    assert notes[-1].what == "1 earlier answer in this chat wasn't used." and notes[-1].why and notes[-1].next_step

    # An answer given while the same types were already withheld is still replayed.
    again = add_turn(session, first.conversation_id, "And the third?", asked_by=None)
    wire3 = pw.Wire()
    wire3.script = [gateway_text_result("Still unknown.")]
    await run_turn(session, again.id, gateway=wire3.client(), today=TODAY)
    sent = wire3.parsed()[0]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"] and sent[1]["content"] == "I can't tell."


@pytest.mark.asyncio
async def test_with_levels_off_earlier_answers_are_replayed_as_before(session, world):
    first = start_conversation(session, "First?", started_by=None)
    wire = pw.Wire()
    wire.script = [gateway_text_result("Earlier answer text.")]
    await run_turn(session, first.id, gateway=wire.client(), today=TODAY)
    follow = add_turn(session, first.conversation_id, "Second?", asked_by=None)
    wire2 = pw.Wire()
    wire2.script = [gateway_text_result("ok")]
    await run_turn(session, follow.id, gateway=wire2.client(), today=TODAY)
    assert [m["role"] for m in wire2.parsed()[0]["messages"]] == ["user", "assistant", "user"]


# --- Ask's own errors follow the messages rule --------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_unreachable_ai_is_explained_in_three_parts_with_the_raw_text_kept_apart(client, session, world):
    gateway = GatewayClient("http://gw.test:8010", "tok",
                            transport=httpx.MockTransport(lambda request: httpx.Response(503, text="upstream overloaded")))
    turn = start_conversation(session, "Anything?", started_by=None)
    done = await run_turn(session, turn.id, gateway=gateway, today=TODAY)

    problem, technical = ask_messages.failure(done)
    assert done.status == AskStatus.FAILED
    assert problem.what == "I couldn't answer that question."
    assert "didn't answer" in problem.why and "try again" in problem.next_step.lower()
    assert "gateway returned 503" in technical and "gateway returned 503" not in problem.text
    page = client.get(f"/ask/c/{done.conversation_id}").text
    assert "answer that question." in page and "Technical details" in page and "Sorry, I couldn" not in page


@pytest.mark.asyncio
async def test_an_unexpected_error_fails_the_turn_visibly_and_is_logged_as_a_problem(session, world):
    class Broken:
        async def run(self, *a, **k):
            raise RuntimeError("boom")

    turn = start_conversation(session, "Anything?", started_by=None)
    done = await run_turn(session, turn.id, gateway=Broken(), today=TODAY)
    assert done.status == AskStatus.FAILED                                   # not left on "looking through the wiki…"
    problem, technical = ask_messages.failure(done)
    assert problem.what and problem.why and problem.next_step and technical == "RuntimeError: boom"
    entry = session.exec(select(ProblemEntry).where(ProblemEntry.ref == f"ask:{turn.id}")).one()
    assert entry.area == "Ask" and entry.next_step and entry.resolved_at is None


def test_failures_stored_before_the_messages_rule_still_render_in_three_parts(session):
    turn = make_turn(session, make_conversation(session), "q", status=AskStatus.FAILED)
    turn.error = "The AI service is unavailable right now."
    problem, _ = ask_messages.failure(turn)
    assert problem.what and problem.why and problem.next_step
