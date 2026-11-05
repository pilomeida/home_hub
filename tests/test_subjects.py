"""Work-list B4: one registry of subjects. The Hub proposes that two names are the same person; a person says yes or
no, once (Pedro, 2026-11-05). Done when Rute is one subject across Health and Money, and Matias one subject seen from
the loan, the payee and the bank lines."""

import pytest
from sqlmodel import select

from app.categories.health.people import find_or_create_person, merge_people
from app.models.bank import BankConnection
from app.models.debt import DebtDirection
from app.models.health import HealthPerson
from app.models.merchant import Merchant
from app.models.person import Person
from app.models.subject import Subject, SubjectMatch
from app.services import subjects as svc
from app.services.debt_ledger import create_informal_debt
from app.services.names import name_key, name_tokens

HTML = {"Accept": "text/html"}


def _connection(session, owner):
    c = BankConnection(bank_name="Santander Totta", country="PT", state=f"s-{owner}", owner=owner)
    session.add(c); session.commit(); session.refresh(c)
    return c


def test_one_name_key_for_the_whole_hub():
    assert name_key("  RUTE  Álvares ") == "rute alvares"
    assert name_tokens("Rute Alexandra da Silva Correia") == ["rute", "alexandra", "silva", "correia"]


def test_rute_is_one_subject_across_health_and_money_once_a_person_says_so(client, session):
    health = find_or_create_person(session, "RUTE ALEXANDRA DA SILVA CORREIA")       # as a health paper names her
    owner = _connection(session, "Rute")                                              # her bank login
    svc.build_registry(session)
    (proposal,) = svc.pending(session)
    assert {proposal.one, proposal.other} == {"“RUTE ALEXANDRA DA SILVA CORREIA” (Health)", "“Rute” (bank connection owner)"}

    page = client.get("/inbox", headers=HTML).text
    assert "Same person?" in page and "RUTE ALEXANDRA DA SILVA CORREIA" in page
    r = client.post(f"/subjects/matches/{proposal.match.id}", data={"same": "yes"})
    assert r.status_code == 200 and "Joined" in r.text

    session.expire_all()
    health, owner = session.get(HealthPerson, health.id), session.get(BankConnection, owner.id)
    assert health.subject_id == owner.owner_subject_id                                # one subject
    kept = session.get(Subject, health.subject_id)
    assert kept.name == "RUTE ALEXANDRA DA SILVA CORREIA" and kept.confirmed           # the fuller name is kept
    assert svc.pending(session) == []


def test_matias_is_one_subject_from_the_loan_the_payee_and_the_bank_lines(client, session):
    debt = create_informal_debt(session, "Matias Correia de Almeida", DebtDirection.OWED_TO_US)
    session.commit()
    payee = Merchant(canonical_name="Matias Almeida", normalized_key="matias almeida")   # as the classifier names him
    session.add(payee); session.commit()
    svc.propose_matches(session); session.commit()
    (proposal,) = svc.pending(session)
    assert proposal.other == "the payee “Matias Almeida”" and "(Loans)" in proposal.one

    assert client.post(f"/subjects/matches/{proposal.match.id}", data={"same": "yes"}).status_code == 200
    session.expire_all()
    person = session.get(Person, debt.person_id)
    assert session.get(Merchant, payee.id).subject_id == person.subject_id            # his bank lines carry this payee


def test_a_no_is_remembered_and_never_asked_again(client, session):
    find_or_create_person(session, "Pedro Nuno dos Santos Almeida")
    _connection(session, "Pedro")
    svc.build_registry(session)
    (proposal,) = svc.pending(session)
    assert client.post(f"/subjects/matches/{proposal.match.id}", data={"same": "no"}).status_code == 200
    session.expire_all()
    assert svc.propose_matches(session) == 0 and svc.pending(session) == []
    assert session.exec(select(SubjectMatch)).one().decision == svc.DIFFERENT
    again = client.post(f"/subjects/matches/{proposal.match.id}", data={"same": "yes"}, headers=HTML)
    assert again.status_code == 409 and "Not saved." in again.text and "Reload the page" in again.text


def test_the_exact_same_name_is_one_subject_without_asking(session):
    a, b = svc.register_person(session, "Pedro"), svc.register_person(session, "  pedro ")
    assert a.id == b.id and session.exec(select(SubjectMatch)).all() == []


def test_merging_two_health_people_joins_their_subjects(session):
    full = find_or_create_person(session, "Vicente Correia de Almeida")
    short = find_or_create_person(session, "Vicente")
    assert full.subject_id != short.subject_id
    merge_people(session, short.id, full.id)
    session.expire_all()
    assert svc.resolve(session, session.get(Subject, short.subject_id)).id == full.subject_id


@pytest.mark.parametrize("payee", ["Galp Energia", "Matias", "Continente Mafra"])
def test_a_payee_that_is_not_a_persons_full_name_is_not_proposed(session, payee):
    create_informal_debt(session, "Matias Correia de Almeida", DebtDirection.OWED_TO_US)
    session.add(Merchant(canonical_name=payee, normalized_key=name_key(payee))); session.commit()
    svc.propose_matches(session)
    assert [p for p in svc.pending(session) if "payee" in p.other] == []


def test_one_question_per_payee_name_and_a_yes_links_every_payee_of_that_name(session):
    create_informal_debt(session, "Matias Correia de Almeida", DebtDirection.OWED_TO_US)
    for key in ("transferencia matias almeida", "trf matias almeida", "mbway matias almeida"):   # how banks write it
        session.add(Merchant(canonical_name="Matias Almeida", normalized_key=key))
    session.commit()
    svc.propose_matches(session); session.commit()
    (proposal,) = svc.pending(session)
    svc.decide(session, proposal.match.id, True, "pedro")
    assert {m.subject_id for m in session.exec(select(Merchant)).all()} == {
        session.exec(select(Person)).one().subject_id}
