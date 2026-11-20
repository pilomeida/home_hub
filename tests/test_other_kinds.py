"""PLAN step 8b: contracts and letters are kinds of their own in Other papers, each with a level on the Privacy page;
the papers filed before move by their own words."""

from app.categories.fields import load_fields
from app.categories.other import refile
from app.models.category import Category
from app.privacy import settings as ps
from app.services import paper_search
from tests.knowledge_factories import make_document


def test_each_kind_has_its_own_row_on_the_privacy_page(session):
    keys = {(r.info.category, r.info.paper_kind) for r in ps.type_rows(session)}
    assert {("other", "contract"), ("other", "letter"), ("other", "other_paper")} <= keys


def test_papers_filed_before_move_by_their_words_and_stay_findable(session):
    contract = make_document(session, category=Category.OTHER, paper_kind="other_paper",
                             fields={"title": "Vodafone - contrato de prestação de serviços", "summary": "s"})
    letter = make_document(session, category=Category.OTHER, paper_kind="other_paper",
                           fields={"title": "Handelsbanken - autocertificação FATCA/CRS", "summary": "Pedido"})
    other = make_document(session, category=Category.OTHER, paper_kind="other_paper",
                          fields={"title": "Certificado de habilitações", "summary": "Diploma"})
    assert sorted((m.document_id, m.to) for m in refile.plan(session)) == [(contract.id, "contract"),
                                                                         (letter.id, "letter")]
    assert other.paper_kind == "other_paper" and load_fields(contract)["title"].startswith("Vodafone")  # nothing yet
    refile.apply(session)
    session.refresh(contract), session.refresh(letter), session.refresh(other)
    assert (contract.paper_kind, letter.paper_kind, other.paper_kind) == ("contract", "letter", "other_paper")
    assert refile.plan(session) == []                                                     # a second run: nothing
    assert [h.document.id for h in paper_search.search_papers(session, "FATCA")] == [letter.id]


def test_the_page_groups_by_kind_and_the_upload_offers_the_kinds(client, session):
    make_document(session, category=Category.OTHER, paper_kind="contract", fields={"title": "EDP contract"})
    make_document(session, category=Category.OTHER, paper_kind="other_paper", fields={"title": "A diploma"})
    page = client.get("/other").text
    assert page.index("<h2>Contracts</h2>") < page.index("EDP contract") < page.index("<h2>Other papers</h2>")
    assert "Letters" not in page                                                     # an empty kind is left out
    form = client.get("/other/upload").text
    assert 'value="contract"' in form and 'value="letter"' in form
