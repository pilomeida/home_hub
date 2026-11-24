"""PLAN step 12e: the papers already filed get names by the same rule. A name is built from what the Hub already holds
of the paper (a bill's provider and period, a statement's bank, month and owner, a policy's insurer and part, a
health paper's provider and person); the AI reads the stored text only where nothing says what it is; a dry run
changes nothing; a second run changes nothing; a paper renamed on approval is left as it is.
Done when: on a made-up Hub, every kind of paper the live Hub holds gets its name, and running again changes none."""

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from app.models.account import Account
from app.models.category import Category
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.health import HealthEntry, HealthPerson
from app.models.paper_text import PaperText
from app.models.subject import Subject, SubjectKind
from app.models.transaction import Transaction
from app.services.paper_search import search_papers
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result

_spec = importlib.util.spec_from_file_location("rename_filed_papers",
                                               Path(__file__).resolve().parent.parent / "scripts/rename_filed_papers.py")
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def _doc(session, filename, category, kind, fields=None, account_id=None, original=None):
    doc = Document(filename=filename, file_path=f"/srv/x/{filename}", content_hash=filename, source=DocumentSource.EMAIL,
                   status=DocumentStatus.PROCESSED, category=category, paper_kind=kind,
                   fields_json=json.dumps(fields or {}), account_id=account_id, original_filename=original)
    session.add(doc)
    session.flush()
    return doc


@pytest.fixture()
def hub(session):
    pedro = Subject(kind=SubjectKind.PERSON, name="Pedro", match_key="pedro", household=True)
    session.add(pedro)
    session.flush()
    revolut = Account(name="Revolut Current Account", institution="Revolut Bank UAB", owner_subject_id=pedro.id)
    session.add(revolut)
    session.flush()
    papers = {}
    papers["bill"] = _doc(session, "fatura_20260923.pdf", Category.FINANCIALS, "bill")
    session.add(Transaction(document_id=papers["bill"].id, description="amigo (Vodafone Portugal)", amount=-30.0,
                            statement_period="2026-09", issue_date=date(2026, 9, 25)))
    papers["statement"] = _doc(session, "revolut-pedro-2025-03.pdf", Category.FINANCIALS, "statement",
                               account_id=revolut.id)
    for day in (date(2025, 3, 1), date(2025, 3, 31)):
        session.add(Transaction(document_id=papers["statement"].id, description="x", amount=-1.0, paid_date=day,
                                account_id=revolut.id))
    papers["policy"] = _doc(session, "Condicoes.pdf", Category.INSURANCE, "policy",
                            {"insurer": "Zurich", "product": "Lar Seguro", "part": "general_conditions",
                             "insured": "Mafra house", "policy_number": "1", "covers": "home"})
    papers["detail"] = _doc(session, "detalhe_fatura_20260623.pdf", Category.FINANCIALS, "bill")
    session.add(PaperText(document_id=papers["detail"].id, text="Vodafone Portugal detalhe da fatura chamadas",
                          method="pdf_text"))
    papers["photo"] = _doc(session, "telegram-photo-AQAD.jpg", Category.FINANCIALS, "bill")
    papers["health"] = _doc(session, "scan.pdf", Category.HEALTH, "lab_result")
    maria = HealthPerson(name="Maria Santos", match_key="maria santos")
    session.add(maria)
    session.flush()
    session.add(HealthEntry(document_id=papers["health"].id, person_id=maria.id, entry_date=date(2026, 3, 12),
                            provider="Unilabs"))
    papers["approved"] = _doc(session, "2024-03-14 · Warranty invoice · Fatura AEG.pdf", Category.HOUSE,
                              "warranty_invoice", {"item_name": "AEG"}, original="AEG Fatura.pdf")
    session.commit()
    return papers


def _gateway(*whats):
    return FakeGateway([gateway_text_result(json.dumps({"what": w, "paper_date": "2026-06-23"})) for w in whats])


@pytest.mark.asyncio
async def test_done_when_every_kind_of_paper_gets_its_name_and_a_second_run_changes_none(session, hub, capsys):
    gateway = _gateway("Fatura Vodafone detalhe de chamadas")
    counts = await script.run(session, apply=True, limit=None, gateway=gateway)
    session.expire_all()
    name = lambda key: session.get(Document, hub[key].id).filename                    # noqa: E731
    assert name("bill") == "2026-09-25 · Bill invoice · amigo (Vodafone Portugal) 2026-09.pdf"
    assert name("statement") == "2025-03-31 · Bank statement · Revolut Current Account 2025-03 · Pedro.pdf"
    assert name("policy").endswith(" · Policy · Zurich Lar Seguro General conditions · Mafra house.pdf")
    assert name("detail") == "2026-06-23 · Bill invoice · Fatura Vodafone detalhe de chamadas.pdf"
    assert name("health") == "2026-03-12 · Lab result · Unilabs · Maria Santos.pdf"
    assert name("photo") == "telegram-photo-AQAD.jpg"                                  # nothing says what it is: kept
    assert name("approved") == "2024-03-14 · Warranty invoice · Fatura AEG.pdf"        # renamed on approval: left
    assert session.get(Document, hub["bill"].id).original_filename == "fatura_20260923.pdf"
    assert counts["renamed"] == 5 and counts["kept"] == 1 and counts["ai"] == 2
    assert len(gateway.requests) == 1                                                  # the photo has no text: no call
    assert hub["bill"].id in {h.document.id for h in search_papers(session, "Vodafone")}
    again = await script.run(session, apply=True, limit=None, gateway=_gateway())
    assert again["renamed"] == 0 and again["papers"] == 1                              # only the photo is looked at


@pytest.mark.asyncio
async def test_a_dry_run_changes_nothing_and_calls_no_ai(session, hub, capsys):
    gateway = _gateway()
    counts = await script.run(session, apply=False, limit=None, gateway=gateway)
    session.expire_all()
    assert session.get(Document, hub["bill"].id).filename == "fatura_20260923.pdf" and not gateway.requests
    out = capsys.readouterr().out
    assert "fatura_20260923.pdf -> 2026-09-25 · Bill invoice · amigo (Vodafone Portugal) 2026-09.pdf [known]" in out
    assert "detalhe_fatura_20260623.pdf -> (the AI says what it is) [AI]" in out and counts["papers"] == 6


@pytest.mark.asyncio
async def test_a_health_paper_is_read_by_the_ai_only_at_high(session, hub):
    session.delete(session.exec(__import__("sqlmodel").select(HealthEntry)).first())
    session.add(PaperText(document_id=hub["health"].id, text="Unilabs analises", method="pdf_text"))
    session.commit()
    gateway = _gateway("", "Analises clinicas Unilabs")
    await script.run(session, apply=True, limit=None, gateway=gateway)
    high = [r for r in gateway.requests if "Unilabs analises" in (r["user"] or "")]
    assert high and high[0]["sensitivity"] == "house_high"


@pytest.mark.asyncio
async def test_what_the_ai_says_is_checked_by_code(session, hub):
    from app.services import paper_filing
    gateway = FakeGateway([gateway_text_result(json.dumps({"what": "  Fatura   " + "x " * 200, "paper_date": "31/02/2026"})),
                           gateway_text_result("not json"), gateway_text_result(json.dumps(["a list"]))])
    first = await paper_filing.ai_parts(session, hub["detail"], gateway)
    assert first["paper_date"] == "" and first["what"].startswith("Fatura x") and len(first["what"]) <= 120
    assert await paper_filing.ai_parts(session, hub["detail"], gateway) == {"what": "", "paper_date": ""}
    assert await paper_filing.ai_parts(session, hub["detail"], gateway) == {"what": "", "paper_date": ""}


def test_a_company_is_named_short_and_a_family_member_by_their_name():
    from app.services.paper_names import short_party, short_who
    assert short_party("MetLife Europe d.a.c. - Sucursal em Portugal") == "MetLife Europe"
    assert short_party("Ligat Telecom, Soc.Unip., Lda.") == "Ligat Telecom"
    assert short_party("Fidelidade - Companhia de Seguros, S.A.") == "Fidelidade"
    assert short_party("Vida Completa (T.A.R. - Seguro de Vida)") == "Vida Completa"
    assert short_party("amigo (Vodafone Portugal)") == "amigo (Vodafone Portugal)"
    assert short_who("Pedro and Rute (both insured death and total or permanent disability)") == "Pedro and Rute"
    assert short_who("Toyota Avensis (82-CL-65)") == "Toyota Avensis (82-CL-65)"


@pytest.mark.asyncio
async def test_the_insurer_is_not_said_twice_and_a_health_paper_names_the_family_member(session, hub):
    from app.services import paper_filing
    hub["policy"].fields_json = json.dumps({"insurer": "Zurich", "product": "Zurich Lar Seguro",
                                            "part": "general_conditions", "insured": "Mafra house"})
    person = session.get(HealthPerson, session.exec(__import__("sqlmodel").select(HealthEntry)).first().person_id)
    person.name = "Pedro Nuno dos Santos Almeida"
    session.add_all([hub["policy"], person])
    session.commit()
    assert paper_filing.known_parts(session, hub["policy"])["what"] == "Zurich Lar Seguro General conditions"
    assert paper_filing.known_parts(session, hub["health"])["who"] == "Pedro"
