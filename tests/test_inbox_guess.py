"""PLAN step 12b: the Hub's guess. One AI call per paper (inside the High run), caption first; it returns the area and
kind (or a new one, marked new), who, items with rooms, tags, a date to act on and the filed name. Code checks every
part before it is kept (Q1). Papers already waiting are guessed again through the same path as a new arrival.
Done when: the Católica diploma (caption "Certificado conclusão gestão TIC Católica - Pedro") is guessed as
Education › Diploma, who Pedro, named "2025-12-15 · Diploma or certificate · ... · Pedro"; the AEG invoice gets its
items with rooms; a paper that fits nothing gets a new area or kind, marked new."""

import json
from datetime import datetime

import pytest

from app.llm_gateway import set_gateway_provider
from app.models.category import Category
from app.models.document import DocumentSource
from app.privacy.ratchet import current_ratchet
from app.privacy.tiers import Tier
from app.services import category_classifier, household, inbox_service, paper_names
from app.services.category_classifier import CONFIDENT_THRESHOLD, suggest_category_and_kind
from app.services.ingestion import IncomingFile
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.fakes.text_pdf import text_pdf_bytes

DIPLOMA_CAPTION = "Certificado conclusão gestão TIC Católica - Pedro"


@pytest.fixture()
def paper(tmp_path):
    path = tmp_path / "telegram-photo-AQAD3A9rG0eu0VF-.pdf"
    path.write_bytes(text_pdf_bytes(["Universidade Catolica Portuguesa", "Certificado", "Pedro Santos Almeida"]))
    return str(path)


async def _guess(session, paper, answer: dict, caption=DIPLOMA_CAPTION):
    household.ensure(session)
    fake = FakeGateway([gateway_text_result(json.dumps(answer))])
    with_session = await suggest_category_and_kind(paper, caption, gateway=fake, session=session)
    return with_session, fake


# --- what the AI is told -------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_prompt_names_the_family_the_rooms_and_puts_the_caption_first(session, paper, levels_not_binding):
    _, fake = await _guess(session, paper, {"domain": None})
    system, user = fake.requests[0]["system"], fake.requests[0]["user"]
    assert "Pedro, Rute, Matias, Vicente" in system
    assert "Kitchen" in system and "Matias' bedroom" in system and "Whole house" in system
    assert "strongest clue" in system and "new_domain" in system and "new_category" in system
    assert user.index(DIPLOMA_CAPTION) < user.index("Classify")                        # the caption comes first


# --- the diploma and the invoice -----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_done_when_the_diploma_is_education_diploma_for_pedro_with_a_plain_name(session, paper, levels_not_binding):
    suggestion, _ = await _guess(session, paper, {
        "domain": "education", "category": "diploma", "confidence": 0.95, "reason": "A certificate of studies.",
        "who": "pedro", "tags": ["education", "postgraduate", "Gestão TIC", "Católica"], "paper_date": "2025-12-15",
        "what": "Certificado conclusão gestão TIC, Católica", "items": [], "act_by": None})
    assert (suggestion.category, suggestion.paper_kind) == (Category.EDUCATION, "diploma")
    guess = suggestion.guess
    assert guess["who"] == "Pedro" and guess["who_is_family"] is True                  # matched to the family list
    assert guess["tags"] == ["education", "postgraduate", "Gestão TIC", "Católica"]
    assert guess["filed_name"] == "2025-12-15 · Diploma or certificate · Certificado conclusao gestao TIC, Catolica · Pedro"


@pytest.mark.asyncio
async def test_the_aeg_invoice_gets_its_items_with_rooms_and_a_warranty_date(session, paper, levels_not_binding):
    suggestion, _ = await _guess(session, paper, {
        "domain": "house", "category": "warranty_invoice", "confidence": 0.9, "reason": "An appliance invoice.",
        "who": None, "paper_date": "2026-09-20", "what": "AEG kitchen appliances",
        "items": [{"name": "Oven", "room": "kitchen"}, {"name": "Induction hob", "room": "Kitchen"},
                  {"name": "Extractor hood", "room": "Cozinha nova"}],
        "act_by": {"date": "2029-09-20", "what": "Warranty ends"}, "tags": ["AEG", "appliances"]},
        caption="Factura electrodomésticos cozinha AEG")
    guess = suggestion.guess
    assert [(i["name"], i["room"], i["new_room"]) for i in guess["items"]] == [
        ("Oven", "Kitchen", False), ("Induction hob", "Kitchen", False), ("Extractor hood", "Cozinha nova", True)]
    assert guess["act_by"] == {"date": "2029-09-20", "what": "Warranty ends"}
    assert guess["who"] is None
    assert guess["filed_name"] == "2026-09-20 · Warranty invoice · AEG kitchen appliances"   # several items: no item; no "/" in a name


@pytest.mark.asyncio
async def test_one_item_is_named_at_the_end(session, paper, levels_not_binding):
    suggestion, _ = await _guess(session, paper, {
        "domain": "house", "category": "warranty_invoice", "paper_date": "2026-09-20", "what": "Worten invoice",
        "items": [{"name": "Dishwasher", "room": "Kitchen"}]})
    assert suggestion.guess["filed_name"] == "2026-09-20 · Warranty invoice · Worten invoice · Dishwasher"


# --- nothing fits: a new area or kind, marked new ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_paper_that_fits_nothing_gets_a_new_area_never_confident(session, paper, levels_not_binding):
    suggestion, _ = await _guess(session, paper, {
        "domain": None, "category": None, "new_domain": "Work", "new_category": "Employment contract",
        "confidence": 0.97, "reason": "A job contract.", "who": "Pedro", "what": "Contrato de trabalho"})
    assert suggestion.category is None and suggestion.confidence < CONFIDENT_THRESHOLD
    assert suggestion.guess["new_area"] == "Work" and suggestion.guess["new_kind"] == "Employment contract"
    assert suggestion.guess["filed_name"].endswith(" · Employment contract · Contrato de trabalho · Pedro")


@pytest.mark.asyncio
async def test_a_new_kind_in_a_known_area(session, paper, levels_not_binding):
    suggestion, _ = await _guess(session, paper, {
        "domain": "financials", "category": None, "new_category": "Contract", "confidence": 0.9,
        "what": "Contrato de adesão MEO"}, caption="Factura Telecom")
    assert suggestion.category == Category.FINANCIALS and suggestion.paper_kind is None
    assert suggestion.guess["new_kind"] == "Contract" and suggestion.confidence < CONFIDENT_THRESHOLD


@pytest.mark.asyncio
async def test_a_new_name_that_already_exists_is_the_existing_one(session, paper, levels_not_binding):
    suggestion, _ = await _guess(session, paper, {
        "domain": None, "new_domain": "education", "new_category": "Diploma or certificate", "confidence": 0.9})
    assert (suggestion.category, suggestion.paper_kind) == (Category.EDUCATION, "diploma")
    assert "new_area" not in suggestion.guess and "new_kind" not in suggestion.guess


# --- the answer is checked before it is kept (Q1) ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_wild_answer_is_cut_down_to_what_can_be_trusted(session, paper, levels_not_binding):
    suggestion, _ = await _guess(session, paper, {
        "domain": "house", "category": "warranty_invoice", "paper_date": "2026-02-31", "who": "x" * 300,
        "items": [{"name": f"Thing {n}", "room": "Garage"} for n in range(30)] + ["not an item"],
        "tags": [f"t{n}" for n in range(25)] + [7, ""], "act_by": {"date": "soon", "what": "pay"}, "what": None})
    guess = suggestion.guess
    assert guess["paper_date"] is None and guess["act_by"] is None                     # not real dates
    assert len(guess["items"]) == 20 and len(guess["tags"]) == 10
    assert len(guess["who"]) <= 80 and guess["who_is_family"] is False


@pytest.mark.asyncio
async def test_an_unknown_area_is_no_guess(session, paper, levels_not_binding):
    suggestion, _ = await _guess(session, paper, {"domain": "spaceships", "category": "rocket"})
    assert suggestion.category is None and suggestion.paper_kind is None


# --- the filed name, one formatter (W2) ----------------------------------------------------------------------------

def test_the_filed_name_has_no_accents_nor_characters_a_file_cannot_hold():
    name = paper_names.filed_name(paper_date="2025-12-15", received=datetime(2026, 9, 28, 10, 42),
                                  kind="Diploma or certificate", what='Gestão TIC: "Católica" / Lisboa?', who="Pedro")
    assert name == "2025-12-15 · Diploma or certificate · Gestao TIC Catolica Lisboa · Pedro"


def test_the_filed_name_falls_back_to_the_day_it_arrived_and_stays_short():
    name = paper_names.filed_name(paper_date=None, received=datetime(2026, 9, 28, 10, 42), kind="Letter",
                                  what="w" * 400, who=None)
    assert name.startswith("2026-09-28 · Letter · ") and len(name) <= 150
    assert paper_names.filed_name(paper_date=None, received=datetime(2026, 9, 28), kind="Letter", what="", who=None) \
        == "2026-09-28 · Letter"


# --- guessing again -------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_waiting_paper_is_guessed_again_inside_the_high_run(session, inbox_documents_dir):
    household.ensure(session)

    async def old_guess(file_path, context_text):
        return category_classifier.CategorySuggestion(None, None, 0.0, "Not sure where this belongs.")
    receipt = await inbox_service.receive_document(
        session, IncomingFile("telegram-photo-AQAD3A9rG0eu0VF-.pdf", text_pdf_bytes(["Certificado"]),
                              DocumentSource.TELEGRAM),
        context_text=f"Telegram caption: {DIPLOMA_CAPTION}", external_ref=None, classify=old_guess)
    seen = {}

    async def new_guess(file_path, context_text, session=None):
        seen["level"] = current_ratchet().tier if current_ratchet() else None
        seen["caption"] = context_text
        return category_classifier.CategorySuggestion(Category.EDUCATION, "diploma", 0.95, "A certificate.",
                                                      {"who": "Pedro", "filed_name": "x"})
    item = await inbox_service.guess_again(session, receipt.document.id, classify=new_guess)
    assert seen == {"level": Tier.HIGH, "caption": f"Telegram caption: {DIPLOMA_CAPTION}"}
    assert (item.suggested_category, item.suggested_paper_kind) == ("education", "diploma")
    assert json.loads(item.guess_json)["who"] == "Pedro"
    assert inbox_service.entry_for(session, receipt.document.id).suggestion_label == "Education › Diploma or certificate"


@pytest.mark.asyncio
async def test_the_card_names_a_new_area_as_new(session, inbox_documents_dir):
    async def guess(file_path, context_text, session=None):
        return category_classifier.CategorySuggestion(None, None, 0.5, "A job contract.",
                                                      {"new_area": "Work", "new_kind": "Employment contract"})
    receipt = await inbox_service.receive_document(
        session, IncomingFile("c.pdf", text_pdf_bytes(["Contrato"]), DocumentSource.EMAIL),
        context_text=None, external_ref=None, classify=guess)
    label = inbox_service.entry_for(session, receipt.document.id).suggestion_label
    assert label == "Work (new area) › Employment contract (new)"


def test_the_script_lists_what_it_would_guess_again_without_calling_the_ai(session):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("guess_waiting_again",
                                                  Path(__file__).resolve().parent.parent / "scripts" / "guess_waiting_again.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.waiting(session) == []                                               # nothing waiting, nothing called


def test_a_long_what_is_cut_to_a_few_words_never_mid_word():
    """Live 2026-11-23: the AI's "what" for a Seguranca Social letter ran to a sentence and the name ended in "Ped"."""
    what = ("Seguranca Social – Notificacao de decisao indeferimento do requerimento de Rendimento Social de Insercao "
            "de Pedro Santos Almeida")
    name = paper_names.filed_name(paper_date="2026-05-08", received=datetime(2026, 9, 28), kind="Benefit decision notice",
                                  what=what, who="Pedro")
    assert name == ("2026-05-08 · Benefit decision notice · Seguranca Social – Notificacao de decisao indeferimento do "
                    "requerimento de Rendimento Social · Pedro")
    long_name = paper_names.filed_name(paper_date=None, received=datetime(2026, 9, 28), kind="k" * 60,
                                       what="palavra " * 12, who="Pedro")
    assert len(long_name) <= 150 and long_name.endswith(" · Pedro") and "palavr ·" not in long_name


def test_the_prompt_asks_for_a_short_what_without_amounts_or_numbers():
    from app.services.category_classifier import build_system_prompt
    prompt = build_system_prompt([])
    assert "at most 8 words" in prompt and "no amounts" in prompt
