import json

from app.models.ask import AskStatus, AskTurn
from app.services.ask.citations import cited_refs, plain_text, render_answer, render_turn
from app.services.ask.contracts import Citable

KNOWN = {
    "wiki:1": Citable("wiki:1", "Wiki: Boiler", "/wiki/1"),
    "doc:7": Citable("doc:7", "House document: service.pdf", "/house/documents/7"),
}


def test_numbers_sources_by_first_appearance_and_drops_unknown():
    text = "Serviced on 2 March 2026 [[doc:7]][[wiki:1]]. Next due 2027 [[wiki:1]] [[doc:99]].\n\nThat's all."
    rendered = render_answer(text, KNOWN)
    assert [n for n, _ in rendered.sources] == [1, 2] and rendered.sources[0][1].ref == "doc:7"
    first = rendered.paragraphs[0]
    assert [s.source_number for s in first if s.source_number] == [1, 2, 2]
    assert "doc:99" not in "".join(s.text for s in first) and len(rendered.paragraphs) == 2


def test_cited_refs_and_plain_text():
    text = "A [[wiki:1]] B [[doc:7]] C [[bogus:1]]"
    assert cited_refs(text, KNOWN) == ["wiki:1", "doc:7"]
    assert plain_text(text, KNOWN) == "A (Wiki: Boiler) B (House document: service.pdf) C"


def test_render_turn_uses_the_turns_own_citations():
    turn = AskTurn(conversation_id=1, position=1, question="q", status=AskStatus.ANSWERED, answer_text="X [[wiki:1]]",
                   citations_json=json.dumps([{"ref": "wiki:1", "label": "Wiki: Boiler", "url": "/wiki/1"}]))
    assert render_turn(turn).sources[0][1].url == "/wiki/1"
