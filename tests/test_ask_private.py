"""Ask with private (Health) records: the first call carries none, touching one raises the run to High for
good, the person is told, and nothing private is saved or replayed below High. Real GatewayClient over a
MockTransport (tests/privacy_world.Wire), so the assertions are about what would leave the VPS."""

import base64
import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlmodel import select

from app.domains.health.extraction import ExtractedHealth, ExtractedMeasurement
from app.domains.health.store import apply_extraction
from app.models.document import Document
from app.models.domain import Domain
from app.models.todo import Todo
from app.models.transaction import Category, Transaction, TransactionType
from app.privacy import settings as ps
from app.privacy.ratchet import RatchetLog
from app.privacy.scope import reachable_scope
from app.privacy.tiers import Tier
from app.services.ask import messages as ask_messages
from app.services.ask.conversation import add_turn, start_conversation
from app.services.ask.engine import run_turn
from app.services.ask.private import chat_is_private, turn_is_private
from app.services.ask.tools import available_tools
from app.services.ask.save import AnswerNotSaveable, save_answer_as_wiki_page
from tests import privacy_world as pw
from tests.fakes.fake_gateway import gateway_text_result, gateway_tool_result
from tests.knowledge_factories import make_claim, make_conversation, make_document, make_link, make_page, make_record, make_turn

TODAY = date(2026, 10, 16)
HEALTH = "SENTINEL-HEALTHREC"


@pytest.fixture()
def home(session):
    pw.set_levels(session, low=["house/house_appliance"])
    clean = make_document(session, domain=Domain.HOUSE, category="house_appliance",
                          fields={"item_name": "Boiler", "room": "Kitchen"}, filename="boiler.png")
    health = make_document(session, domain=Domain.HEALTH, category="lab_result", filename="lab.pdf",
                           fields={"person": "Maria", "provider": "Lab Norte", "summary": f"{HEALTH}-summary"})
    page = make_page(session, f"Maria: tests {HEALTH}", domain=Domain.HEALTH, summary=f"{HEALTH}-page")
    make_claim(session, page, "cholesterol", f"{HEALTH}-210", doc_ids=[health.id])
    return SimpleNamespace(clean=clean, health=health, page=page)


def _tool(name, **args):
    return gateway_tool_result([{"id": "t1", "name": name, "input": args}])


async def ask(session, wire, script, question="What was Maria's last lab result?"):
    wire.script = list(script)
    turn = start_conversation(session, question, started_by=None)
    return await run_turn(session, turn.id, gateway=wire.client(), today=TODAY)


@pytest.mark.asyncio
async def test_a_question_that_needs_no_private_record_stays_below_high_and_sends_none(session, home):
    wire = pw.Wire()
    turn = await ask(session, wire, [gateway_text_result("The boiler is in the Kitchen.")], "Where is the boiler?")
    bodies = wire.parsed()
    assert [b["sensitivity"] for b in bodies] == ["house_low"]
    assert HEALTH not in json.dumps(bodies) and "Lab Norte" not in json.dumps(bodies)
    assert turn.escalation_json is None and turn_is_private(turn) is False
    assert ask_messages.notices(turn) == []


@pytest.mark.asyncio
async def test_the_first_call_names_no_private_content_but_says_private_records_exist(session, home):
    wire = pw.Wire()
    await ask(session, wire, [gateway_text_result("ok")])
    system = wire.parsed()[0]["system"]
    assert "PRIVATE" in system and "health_" in system
    assert HEALTH not in system and "Maria" not in system and "Lab Norte" not in system


@pytest.mark.asyncio
async def test_using_a_private_record_raises_the_run_to_high_for_every_later_call(session, home):
    wire = pw.Wire()
    turn = await ask(session, wire, [
        _tool("find_sources", query="Maria lab"),
        gateway_text_result(f"Maria's result is in lab.pdf [[doc:{home.health.id}]]."),
    ])
    first, second = wire.parsed()
    assert first["sensitivity"] == "house_low" and HEALTH not in json.dumps(first)
    assert second["sensitivity"] == "house_high" and HEALTH in json.dumps(second)     # the private content went only here
    log = RatchetLog.from_json(turn.escalation_json)
    assert log.private and not log.started_private
    esc = log.first_private()
    assert (esc.step, esc.from_tier, esc.to_tier) == (1, "house_low", "house_high")


@pytest.mark.asyncio
async def test_the_person_is_told_explicitly_that_and_when_it_turned_private(session, home):
    wire = pw.Wire()
    turn = await ask(session, wire, [_tool("find_sources", query="Maria"), gateway_text_result("done [[doc:%d]]" % home.health.id)])
    note = ask_messages.notices(turn)[0]
    assert note.what == "This answer turned private."
    assert "step 1" in note.why and "private" in note.why.lower() and "no other AI service" in note.why
    assert note.next_step


@pytest.mark.asyncio
async def test_it_works_the_same_while_levels_are_not_binding(session, home):
    pw.set_levels(session, on=False)
    wire = pw.Wire()
    await ask(session, wire, [_tool("find_sources", query="Maria"), gateway_text_result("done")])
    first, second = wire.parsed()
    assert "sensitivity" not in first and HEALTH not in json.dumps(first)
    assert second["sensitivity"] == "house_high"


@pytest.mark.asyncio
async def test_reading_a_private_wiki_page_raises_the_run_too(session, home):
    wire = pw.Wire()
    turn = await ask(session, wire, [_tool("read_wiki_pages", page_ids=[home.page.id]), gateway_text_result("ok")])
    assert [b["sensitivity"] for b in wire.parsed()] == ["house_low", "house_high"]
    assert turn_is_private(turn)


@pytest.mark.asyncio
async def test_a_follow_up_in_a_private_chat_starts_high_and_says_so(session, home):
    wire = pw.Wire()
    first = await ask(session, wire, [_tool("find_sources", query="Maria"), gateway_text_result("done")])
    wire.script = [gateway_text_result("And the boiler is in the Kitchen.")]
    second = add_turn(session, first.conversation_id, "And where is the boiler?", None)
    second = await run_turn(session, second.id, gateway=wire.client(), today=TODAY)
    last = wire.parsed()[-1]
    assert last["sensitivity"] == "house_high"                                   # the history carries private content
    assert chat_is_private(session, second) is True
    assert RatchetLog.from_json(second.escalation_json).started_private
    assert ask_messages.notices(second)[0].what == "This answer ran privately from the start."


@pytest.mark.asyncio
async def test_a_private_answer_cannot_be_saved_to_the_wiki(session, home):
    wire = pw.Wire()
    turn = await ask(session, wire, [_tool("find_sources", query="Maria"), gateway_text_result("done [[doc:%d]]" % home.health.id)])
    with pytest.raises(AnswerNotSaveable) as exc:
        save_answer_as_wiki_page(session, turn.id, "Maria")
    text = str(exc.value)
    assert text.startswith("Not saved") and "private" in text and "chat" in text      # what, why, what to do




# --- the tripwire: every tool, over the privacy world ---------------------------------------------------

HEALTHTODO = "SENTINEL-HEALTHTODO"


def _health_paper(session, docs_dir) -> Document:
    """A filed Health paper, a real PNG, read into Health's tables. HEALTH sits in its summary, the measurement's
    name and the person's name, so each Health tool's output carries the sentinel."""
    docs_dir.mkdir(parents=True, exist_ok=True)
    path = docs_dir / "maria-lab.png"
    path.write_bytes(pw.PNG)
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result", filename="maria-lab.png",
                        file_path=str(path))
    apply_extraction(session, doc, ExtractedHealth(
        person_name=f"Maria {HEALTH}", document_date=date(2026, 3, 12), provider="Lab Norte", record_kind="test",
        areas=["Heart and circulation"], conditions=[], summary=f"Maria: lipid panel {HEALTH}-summary",
        measurements=[ExtractedMeasurement(f"LDL cholesterol {HEALTH}", 162.0, None, "mg/dL", None, 100.0,
                                           date(2026, 3, 12), False, "LDL")]))
    return doc


@pytest.fixture()
def world(session, inbox_documents_dir):
    w = pw.build_world(session, inbox_documents_dir)
    session.add_all([Todo(title=HEALTHTODO, domain=Domain.HEALTH),                       # no linked source at all
                     Todo(title=f"{pw.HIGH}-todo", document_id=w.c.id)])                 # made from a High document
    session.commit()
    w.health = _health_paper(session, inbox_documents_dir)
    return w


def _calls(w) -> dict:
    """Arguments for EVERY tool; a tool with no entry fails the tripwire until someone adds it."""
    return {
        "read_wiki_pages": [{"page_ids": [p.id for p in w.pages]}, {"page_ids": [w.page_clean.id]}],
        "find_sources": [{"limit": 60}, {"query": "insurer policy notes statement deed item Boiler", "limit": 60}],
        "read_document_file": [{"document_id": d.id} for d in (w.a, w.c, w.d, w.e, w.unk, w.health)],
        "list_todos": [{"include_done": True}],
        "financials_spending": [{"date_from": "2026-01-01", "date_to": "2026-12-31", "group_by": g}
                                for g in ("merchant", "category", "month")],
        "financials_find_transactions": [{"query": ""}],
        "health_find_records": [{"query": "Maria"}],
        "health_measurements": [{}],
    }


_STATEMENT_HIGH = {**pw.STANDARD, "high": [*pw.STANDARD["high"], "financials/statement"]}
_HEALTH_TOOLS = {"health_find_records", "health_measurements"}
_SCENARIOS = {
    # name: (levels, private sentinels, private files, tools that must be seen returning private content)
    "standard": (pw.STANDARD, [*pw.PRIVATE_DERIVED, HEALTHTODO, HEALTH], pw.PRIVATE_FILES,
                 {"read_wiki_pages", "find_sources", "read_document_file", "list_todos", *_HEALTH_TOOLS}),
    "statement_is_high": (_STATEMENT_HIGH, [*pw.PRIVATE_DERIVED, HEALTHTODO, HEALTH, pw.UNSET, pw.TXN, pw.HISTORY, pw.TODO, pw.SUMMARY],
                          [*pw.PRIVATE_FILES, b"%PDF " + pw.UNSET.encode()],
                          {"read_wiki_pages", "find_sources", "read_document_file", "list_todos",
                           "financials_spending", "financials_find_transactions", *_HEALTH_TOOLS}),
}


@pytest.mark.parametrize("scenario", list(_SCENARIOS))
def test_every_tool_that_returns_private_content_reports_high(session, world, scenario):
    """The tripwire: a tool whose output holds a private sentinel (in its text, or as a base64 file) MUST say
    tier=HIGH, or the engine would send it on at a lower level. No tool is skipped."""
    levels, sentinels, files, must_hit = _SCENARIOS[scenario]
    pw.set_levels(session, **levels)
    calls, hit = _calls(world), set()
    with reachable_scope(session):
        for tool in available_tools():
            assert tool.name in calls, f"add tripwire arguments for {tool.name}"
            for args in calls[tool.name]:
                out = tool.run(session, args)
                seen = out.text + json.dumps(out.content_blocks)
                if any(x in seen for x in sentinels) or any(base64.b64encode(f).decode() in seen for f in files):
                    hit.add(tool.name)
                    assert out.tier == Tier.HIGH, f"{tool.name}{args} returned private content without tier=HIGH"
                    assert out.opened, f"{tool.name}{args} did not say what it opened"
                if scenario == "statement_is_high" and tool.name.startswith("financials_"):
                    assert out.tier == Tier.HIGH, f"{tool.name}{args}: aggregates of a High statement are private too"
    assert hit >= must_hit, f"positive control failed, never saw private content from {must_hit - hit}"


def test_a_health_scan_read_as_a_file_is_high_and_carries_the_image(session, world):
    """The real PNG of a Health paper, read as a file: the answer carries the image, so the run is High."""
    pw.set_levels(session, **pw.STANDARD)
    with reachable_scope(session):
        out = next(t for t in available_tools() if t.name == "read_document_file").run(session, {"document_id": world.health.id})
    assert out.tier == Tier.HIGH and out.opened == "1 private record"
    assert base64.b64encode(pw.PNG).decode() in json.dumps(out.content_blocks)


# --- fix round 1 -------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_financials_type_set_high_makes_the_transactions_private_on_the_wire(session, world):
    pw.set_levels(session, **_STATEMENT_HIGH)
    wire = pw.Wire()
    wire.script = [_tool("financials_find_transactions", query=""), gateway_text_result("ok")]
    turn = start_conversation(session, "What did we pay?", started_by=None)
    turn = await run_turn(session, turn.id, gateway=wire.client(), today=TODAY)
    (high,) = [b for b in wire.parsed() if b["sensitivity"] == "house_high"]
    assert pw.TXN in json.dumps(high)
    assert wire.parsed()[0]["sensitivity"] == "house_mid" and pw.TXN not in json.dumps(wire.parsed()[0])
    assert turn_is_private(turn) and "private transaction" in RatchetLog.from_json(turn.escalation_json).first_private().label


@pytest.mark.asyncio
async def test_reading_only_a_clean_page_that_links_a_private_page_raises_the_run(session, world):
    pw.set_levels(session, **pw.STANDARD)
    wire = pw.Wire()
    wire.script = [_tool("read_wiki_pages", page_ids=[world.page_clean.id]), gateway_text_result("ok")]
    turn = start_conversation(session, "Tell me about the boiler", started_by=None)
    turn = await run_turn(session, turn.id, gateway=wire.client(), today=TODAY)
    first, second = wire.parsed()
    assert pw.NEIGHBOUR not in json.dumps(first)                                   # the title is not in the first call
    assert second["sensitivity"] == "house_high" and pw.NEIGHBOUR in json.dumps(second)
    assert "1 private wiki page" in RatchetLog.from_json(turn.escalation_json).first_private().label


def test_a_record_with_a_private_attached_file_is_private_in_find_sources(session, inbox_documents_dir):
    pw.set_levels(session, low=["house/maintenance_log"], high=["insurance/home_insurance"])
    doc = make_document(session, domain=Domain.INSURANCE, category="home_insurance", filename="policy.pdf",
                        file_path=str(inbox_documents_dir / "policy.pdf"), fields={"insurer": "Acme"})
    make_record(session, domain=Domain.HOUSE, category="maintenance_log", document_id=doc.id,
                fields={"item_name": "Boiler", "notes": "serviced"})
    with reachable_scope(session):
        out = next(t for t in available_tools() if t.name == "find_sources").run(session, {"query": "boiler"})
    assert "serviced" in out.text and out.tier == Tier.HIGH and out.opened == "1 private record"


def test_an_unlinked_health_todo_is_private(session):
    session.add(Todo(title="Book the dentist", domain=Domain.HEALTH))
    session.commit()
    with reachable_scope(session):
        out = next(t for t in available_tools() if t.name == "list_todos").run(session, {})
    assert "Book the dentist" in out.text and out.tier == Tier.HIGH and out.opened == "1 private to-do"


@pytest.mark.asyncio
async def test_a_follow_up_starts_high_when_an_earlier_answer_cites_a_source_that_is_private_now(session, home):
    wire = pw.Wire()
    first = await ask(session, wire, [_tool("find_sources", query="boiler"),
                                      gateway_text_result(f"The boiler is in the Kitchen [[doc:{home.clean.id}]].")],
                      "Where is the boiler?")
    assert not turn_is_private(first) and [b["sensitivity"] for b in wire.parsed()] == ["house_low", "house_low"]

    ps.save_type(session, "house", "house_appliance", "house_high", False, "test")        # it became High later
    wire.script = [gateway_text_result("Still the Kitchen.")]
    follow = add_turn(session, first.conversation_id, "And the room?", None)
    follow = await run_turn(session, follow.id, gateway=wire.client(), today=TODAY)
    assert wire.parsed()[-1]["sensitivity"] == "house_high"
    assert RatchetLog.from_json(follow.escalation_json).started_private


@pytest.mark.asyncio
async def test_a_follow_up_starts_high_when_an_earlier_answer_cites_a_wiki_page_with_a_claim_that_is_private_now(session, home):
    page = make_page(session, "Boiler", domain=Domain.HOUSE, summary="Gas boiler")
    make_claim(session, page, "room", "Kitchen", doc_ids=[home.clean.id])
    wire = pw.Wire()
    first = await ask(session, wire, [gateway_text_result(f"Kitchen [[wiki:{page.id}]].")], "Where is the boiler?")
    assert not turn_is_private(first) and chat_is_private(session, first) is False

    ps.save_type(session, "house", "house_appliance", "house_high", False, "test")
    wire.script = [gateway_text_result("Same.")]
    follow = add_turn(session, first.conversation_id, "And the room?", None)
    follow = await run_turn(session, follow.id, gateway=wire.client(), today=TODAY)
    assert wire.parsed()[-1]["sensitivity"] == "house_high"


@pytest.mark.asyncio
async def test_one_round_that_opens_several_private_things_is_logged_as_one_escalation(session, home):
    wire = pw.Wire()
    calls = gateway_tool_result([{"id": "t1", "name": "find_sources", "input": {"query": "Maria"}},
                                 {"id": "t2", "name": "read_wiki_pages", "input": {"page_ids": [home.page.id]}}])
    turn = await ask(session, wire, [calls, gateway_text_result("ok")])
    log = RatchetLog.from_json(turn.escalation_json)
    assert len(log.escalations) == 1 and " and " in log.first_private().label


def test_the_notices_say_private_records_with_correct_grammar():
    from app.privacy.ratchet import Escalation, Ratchet
    from app.models.ask import AskTurn
    from app.services.ask.private import private_notices
    for label in ("1 private record", "3 private records", "2 private records and 1 private wiki page"):
        r = Ratchet()
        r.raise_to(Tier.HIGH, step=2, label=label)
        (note,) = private_notices(AskTurn(conversation_id=1, position=1, question="q", escalation_json=r.to_json()))
        assert f"opened {label}." in note.why and "Health" not in note.why and "which is" not in note.why
    r = Ratchet(tier=Tier.HIGH, started_private=True)
    (note,) = private_notices(AskTurn(conversation_id=1, position=1, question="q", escalation_json=r.to_json()))
    assert "Health" not in note.why


def test_a_tool_that_crashes_does_not_echo_its_error_into_the_untiered_result(session):
    from app.services.ask.contracts import AskTool
    from app.services.ask.engine import _run_tool

    def boom(session, args):
        raise RuntimeError(f"row {HEALTH}-secret exploded")
    out = _run_tool(session, AskTool("boom", "d", {"type": "object"}, boom), "boom", {})
    assert out.is_error and out.tier is None and HEALTH not in out.text and "exploded" not in out.text


def test_saving_a_private_answer_is_refused_with_the_three_part_message(client, session):
    conv = make_conversation(session)
    turn = make_turn(session, conv, "q", answer_text="a")
    turn.escalation_json = json.dumps({"started_private": True, "escalations": []})
    session.add(turn)
    session.commit()
    r = client.post(f"/ask/turns/{turn.id}/save", data={"title": "x"})
    assert r.status_code == 400
    assert "Not saved to the wiki." in r.text and "private" in r.text and "Keep it in this chat" in r.text


def _earlier_answer_citing(session, ref):
    conv = make_conversation(session)
    turn = make_turn(session, conv, "q1", answer_text="a1", citations=[{"ref": ref, "label": "x", "url": "/x"}])
    return add_turn(session, conv.id, "q2", None), turn


def test_an_earlier_answer_citing_a_todo_whose_document_is_private_now_makes_the_chat_private(session, home):
    todo = Todo(title="Service the boiler", document_id=home.clean.id)
    session.add(todo)
    session.commit()
    follow, _ = _earlier_answer_citing(session, f"todo:{todo.id}")
    assert chat_is_private(session, follow) is False
    ps.save_type(session, "house", "house_appliance", "house_high", False, "test")
    assert chat_is_private(session, follow) is True


def test_an_earlier_answer_citing_a_todo_that_no_longer_exists_is_not_private(session, home):
    follow, _ = _earlier_answer_citing(session, "todo:9999")
    assert chat_is_private(session, follow) is False


def test_an_earlier_answer_citing_a_transaction_of_a_statement_that_is_high_now_makes_the_chat_private(session, world):
    pw.set_levels(session, **pw.STANDARD)
    txn = session.exec(select(Transaction).where(Transaction.document_id == world.d.id)).first()
    clean = session.exec(select(Transaction).where(Transaction.document_id == world.b.id)).first()
    follow, _ = _earlier_answer_citing(session, f"financials:txn:{clean.id}")
    assert chat_is_private(session, follow) is False
    follow2, _ = _earlier_answer_citing(session, f"financials:txn:{txn.id}")
    ps.save_type(session, "financials", "statement", "house_high", False, "test")
    assert chat_is_private(session, follow2) is True                          # the statement is High now
    assert chat_is_private(session, follow) is False                          # the bill is not


@pytest.mark.parametrize("raw", ["42", "{}", '"x"', "null5", "[1]", '[{"label": "no ref"}]', "[42]"])
def test_a_citation_record_of_an_unexpected_shape_reads_as_private(session, home, raw):
    conv = make_conversation(session)
    turn = make_turn(session, conv, "q1", answer_text="a1")
    turn.citations_json = raw
    session.add(turn)
    session.commit()
    follow = add_turn(session, conv.id, "q2", None)
    assert chat_is_private(session, follow) is True


def test_find_sources_goes_out_high_when_a_private_match_is_below_the_cut(session):
    """The header says "N matching (showing M)": N counts every match, so one private match below the limit
    still makes the output High, or the count would leak at a lower level."""
    make_document(session, domain=Domain.HEALTH, category="lab_result", filename="lab.pdf",
                  fields={"person": "Maria", "summary": "a lab"}, created_at=datetime(2026, 1, 1))
    for n in range(3):
        make_document(session, domain=Domain.HOUSE, category="house_appliance", filename=f"h{n}.png",
                      fields={"item_name": f"Boiler {n}"}, created_at=datetime(2026, 5, 1 + n))
    find = next(t for t in available_tools() if t.name == "find_sources")
    with reachable_scope(session):
        out = find.run(session, {"limit": 2})
    assert out.text.startswith("4 matching filed sources (showing 2):")
    assert "lab.pdf" not in out.text and f"doc:" in out.text            # the private paper is below the cut
    assert out.tier == Tier.HIGH and out.opened
