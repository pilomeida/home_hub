"""PLAN step 10i: the Who chip. Everyone, or one person from the subjects registry, on Papers, Coming up, Waiting for
you and Ask; a filter for whoever is looking (a cookie), hiding nothing from anyone's login."""

from datetime import date, timedelta

from app.main import app
from app.models.account import Account
from app.models.category import Category
from app.models.subject import Subject, SubjectKind
from app.models.transaction import Transaction, TransactionType
from app.routers.ask import get_ask_gateway
from app.services import who
from tests.knowledge_factories import make_document


def _two_people(session):
    rute = Subject(kind=SubjectKind.PERSON, name="Rute", match_key="rute")
    pedro = Subject(kind=SubjectKind.PERSON, name="Pedro", match_key="pedro")
    alias = Subject(kind=SubjectKind.PERSON, name="RUTE ALEXANDRA", match_key="rute alexandra")
    session.add_all([rute, pedro, alias]); session.commit()
    alias.merged_into_id = rute.id
    acc = Account(name="Santander Credit Card (Rute)", institution="Santander", owner_subject_id=alias.id)   # an alias counts as its person
    session.add_all([alias, acc]); session.commit()
    hers = make_document(session, category=Category.FINANCIALS, paper_kind="bill", filename="rute-card.pdf")
    other = make_document(session, category=Category.FINANCIALS, paper_kind="bill", filename="house-water.pdf")
    session.add(Transaction(document_id=hers.id, account_id=acc.id, description="RUTE CARD", amount=30.0,
                            transaction_type=TransactionType.DEBIT, currency="EUR",
                            due_date=date.today() + timedelta(days=4)))
    session.add(Transaction(document_id=other.id, description="SMAS WATER", amount=20.0,
                            transaction_type=TransactionType.DEBIT, currency="EUR",
                            due_date=date.today() + timedelta(days=5)))
    session.commit()
    return rute, pedro, hers, other


def test_people_come_from_the_registry_and_an_alias_is_its_person(session):
    rute, pedro, hers, _ = _two_people(session)
    assert [p.name for p in who.people(session)] == ["Pedro", "Rute"]
    assert who.document_ids(session, rute.id) == {hers.id} and who.document_ids(session, pedro.id) == set()
    assert who.chosen(session, "abc") is None and who.chosen(session, "999") is None


def test_choosing_a_person_filters_papers_and_coming_up_and_everyone_shows_all(client, session, engine, monkeypatch):
    monkeypatch.setattr("app.db.engine", engine)                 # the header chip opens its own session
    rute, _, _, _ = _two_people(session)
    r = client.get(f"/who?id={rute.id}&back=/papers", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/papers" and r.cookies.get("who") == str(rute.id)
    client.cookies.clear(); client.cookies.set("who", r.cookies.get("who"))   # the browser sends it back
    papers = client.get("/papers").text
    assert "rute-card.pdf" in papers and "house-water.pdf" not in papers and "Who: Rute" in papers
    coming = client.get("/financials/coming-up").text
    assert "RUTE CARD" in coming and "SMAS WATER" not in coming and "about nobody in particular left out" in coming
    gone = client.get("/who?back=/papers", follow_redirects=False)
    assert 'who=""' in gone.headers["set-cookie"] or "Max-Age=0" in gone.headers["set-cookie"]
    client.cookies.clear()
    everyone = client.get("/papers").text
    assert "house-water.pdf" in everyone and "Who: Everyone" in everyone


def test_back_never_leaves_the_site(client, session):
    for bad in ("https://evil.example", "//evil.example"):
        assert client.get(f"/who?back={bad}", follow_redirects=False).headers["location"] == "/"


def test_ask_says_who_the_question_is_about(client, session):
    rute, _, _, _ = _two_people(session)

    class Never:
        async def run(self, *a, **k):
            raise AssertionError("not reached")
    app.dependency_overrides[get_ask_gateway] = lambda: Never()
    try:
        client.cookies.set("who", str(rute.id))
        client.post("/ask", data={"question": "When is the card due?"}, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(get_ask_gateway, None)
        client.cookies.clear()
    from sqlmodel import select
    from app.models.ask import AskTurn
    assert session.exec(select(AskTurn)).first().question == "About Rute: When is the card due?"


def test_waiting_for_you_shows_only_items_about_the_persons_papers(client, session):
    from sqlmodel import select as sel
    from app.services import to_check
    rute, pedro, hers, other = _two_people(session)
    for t in session.exec(sel(Transaction)).all():               # both lines held for checking, as a reading does
        t.review_status, t.review_reason = to_check.NEEDS_CHECK, "The amount was hard to read."
        session.add(t)
    session.commit()
    everyone = client.get("/waiting?area=money").text
    assert "RUTE CARD" in everyone and "SMAS WATER" in everyone
    client.cookies.set("who", str(rute.id))
    try:
        html = client.get("/waiting?area=money").text
    finally:
        client.cookies.clear()
    assert "Showing what is about <b>Rute</b>" in html and "RUTE CARD" in html and "SMAS WATER" not in html
