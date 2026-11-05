"""The subjects registry (work-list B4): register a name once, propose the matches, join on a person's yes.

A new name joins an existing subject only when its key is exactly the same (app/services/names.py). Anything
looser is a proposal (Pedro, 2026-11-05: propose, a person confirms): two people whose names share the first name
and one is contained in the other ("Rute" / "Rute Alexandra da Silva Correia"), or a payee whose name is a person's
("Matias Almeida" / "Matias Correia de Almeida"). A yes joins them for good; a no is remembered and never asked again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.models.account import Account
from app.models.bank import BankAccountLink, BankConnection
from app.models.health import HealthPerson
from app.models.merchant import Merchant
from app.models.person import Person
from app.models.subject import Subject, SubjectKind, SubjectMatch
from app.problems import Problem
from app.services.names import name_key, name_tokens

SAME, DIFFERENT = "same", "different"
# The stores that point at a person's subject: (model, column, how the place is named to a person).
_LINKS = ((Person, "subject_id", "Loans"), (HealthPerson, "subject_id", "Health"),
          (BankConnection, "owner_subject_id", "bank connection owner"), (Account, "owner_subject_id", "account owner"),
          (Merchant, "subject_id", "payee"))


class SubjectError(Exception):
    def __init__(self, problem: Problem):
        super().__init__(problem.text)
        self.problem = problem


def resolve(session: Session, subject: Subject) -> Subject:
    """Follow a merged-away subject to the one kept."""
    seen = {subject.id}
    while subject.merged_into_id is not None and subject.merged_into_id not in seen:
        nxt = session.get(Subject, subject.merged_into_id)
        if nxt is None:
            break
        seen.add(nxt.id)
        subject = nxt
    return subject


def register_person(session: Session, name: Optional[str]) -> Optional[Subject]:
    """The person's subject: the one with exactly this name key, or a new one; then the matches are proposed."""
    key = name_key(name or "")
    if not key:
        return None
    found = session.exec(select(Subject).where(Subject.kind == SubjectKind.PERSON, Subject.match_key == key)
                         .order_by(Subject.id)).first()
    if found is not None:
        return resolve(session, found)
    subject = Subject(kind=SubjectKind.PERSON, name=name.strip(), match_key=key)
    session.add(subject)
    session.flush()
    propose_matches(session)
    return subject


def _people(session: Session) -> list[Subject]:
    return list(session.exec(select(Subject).where(Subject.kind == SubjectKind.PERSON,
                                                   Subject.merged_into_id.is_(None)).order_by(Subject.id)).all())


def _alike(a: list[str], b: list[str]) -> bool:
    """Same first name, and one name's words all in the other's."""
    return bool(a) and bool(b) and a[0] == b[0] and (set(a) <= set(b) or set(b) <= set(a))


def _asked(session: Session, subject_id: int, other_id: Optional[int] = None, merchant_id: Optional[int] = None) -> bool:
    pairs = [(subject_id, other_id), (other_id, subject_id)] if other_id is not None else [(subject_id, None)]
    for s_id, o_id in pairs:
        if session.exec(select(SubjectMatch.id).where(SubjectMatch.subject_id == s_id, SubjectMatch.other_subject_id == o_id,
                                                      SubjectMatch.merchant_id == merchant_id)).first() is not None:
            return True
    return False


def propose_matches(session: Session) -> int:
    """Add a proposal for every likely pair not asked about yet. Returns how many were added."""
    people = _people(session)
    added = 0
    for i, a in enumerate(people):
        a_words = name_tokens(a.name)
        for b in people[i + 1:]:
            if _alike(a_words, name_tokens(b.name)) and not _asked(session, a.id, b.id):
                session.add(SubjectMatch(subject_id=a.id, other_subject_id=b.id,
                                         reason=f"“{a.name}” and “{b.name}” share a first name, and one name is "
                                                f"part of the other."))
                added += 1
        # One question per payee NAME: the same name is often several payees (one per way the bank writes it).
        asked_names = {name_key(session.get(Merchant, m.merchant_id).canonical_name)
                       for m in session.exec(select(SubjectMatch).where(SubjectMatch.subject_id == a.id,
                                                                        SubjectMatch.merchant_id.is_not(None))).all()}
        for merchant in session.exec(select(Merchant).where(Merchant.subject_id.is_(None)).order_by(Merchant.id)).all():
            m_words, m_key = name_tokens(merchant.canonical_name), name_key(merchant.canonical_name)
            if (len(m_words) >= 2 and a_words and m_words[0] == a_words[0] and set(m_words) <= set(a_words)
                    and m_key not in asked_names):
                asked_names.add(m_key)
                session.add(SubjectMatch(subject_id=a.id, merchant_id=merchant.id,
                                         reason=f"The payee “{merchant.canonical_name}” has the name of “{a.name}”."))
                added += 1
    session.flush()
    return added


@dataclass(frozen=True)
class Proposal:
    match: SubjectMatch
    one: str               # "“Rute” (bank connection owner)"
    other: str


def _seen_as(session: Session, subject: Subject) -> str:
    places = sorted({label for model, column, label in _LINKS
                     if session.exec(select(model).where(getattr(model, column) == subject.id)).first() is not None})
    return f"“{subject.name}”" + (f" ({', '.join(places)})" if places else "")


def pending(session: Session) -> list[Proposal]:
    """The proposals waiting for a person, as they read on the page; one whose two sides are already one is dropped."""
    out = []
    for match in session.exec(select(SubjectMatch).where(SubjectMatch.decision.is_(None)).order_by(SubjectMatch.id)).all():
        one = resolve(session, session.get(Subject, match.subject_id))
        if match.merchant_id is not None:
            merchant = session.get(Merchant, match.merchant_id)
            if merchant is None or merchant.subject_id is not None:
                continue
            out.append(Proposal(match, _seen_as(session, one), f"the payee “{merchant.canonical_name}”"))
            continue
        other = resolve(session, session.get(Subject, match.other_subject_id))
        if other.id != one.id:
            out.append(Proposal(match, _seen_as(session, one), _seen_as(session, other)))
    return out


def merge(session: Session, source_id: int, target_id: int) -> Subject:
    """Join `source` into `target`: every link to it now leads to `target`; `source` stays as an alias."""
    source, target = resolve(session, session.get(Subject, source_id)), resolve(session, session.get(Subject, target_id))
    if source.id == target.id:
        return target
    for model, column, _label in _LINKS:
        for row in session.exec(select(model).where(getattr(model, column) == source.id)).all():
            setattr(row, column, target.id)
            session.add(row)
    source.merged_into_id = target.id
    target.confirmed = True
    session.add(source)
    session.add(target)
    return target


def decide(session: Session, match_id: int, same: bool, by: Optional[str]) -> None:
    match = session.get(SubjectMatch, match_id)
    if match is None or match.decision is not None:
        raise SubjectError(Problem("Not saved.", "That question was already answered, or no longer exists.",
                                   "Reload the page to see what is still waiting."))
    if same and match.merchant_id is not None:            # every payee of that name is this person
        key = name_key(session.get(Merchant, match.merchant_id).canonical_name)
        subject_id = resolve(session, session.get(Subject, match.subject_id)).id
        for merchant in session.exec(select(Merchant).where(Merchant.subject_id.is_(None))).all():
            if name_key(merchant.canonical_name) == key:
                merchant.subject_id = subject_id
                session.add(merchant)
    elif same:
        one = resolve(session, session.get(Subject, match.subject_id))
        other = resolve(session, session.get(Subject, match.other_subject_id))
        keep, drop = (one, other) if len(name_tokens(one.name)) >= len(name_tokens(other.name)) else (other, one)
        merge(session, drop.id, keep.id)                  # the fuller name is kept; the other is an alias
    match.decision, match.decided_by, match.decided_at = (SAME if same else DIFFERENT), by, datetime.utcnow()
    session.add(match)
    session.commit()


def link_account_owners(session: Session) -> int:
    """An account's owner is its bank connection's owner."""
    n = 0
    for link, connection in session.exec(select(BankAccountLink, BankConnection)
                                         .join(BankConnection, BankConnection.id == BankAccountLink.connection_id)
                                         .where(BankAccountLink.account_id.is_not(None))).all():
        account = session.get(Account, link.account_id)
        if account is not None and connection.owner_subject_id and account.owner_subject_id is None:
            account.owner_subject_id = connection.owner_subject_id
            session.add(account)
            n += 1
    return n


def build_registry(session: Session, *, commit: bool = True) -> dict[str, int]:
    """Register every name the Hub already holds (idempotent): Loans' people, Health's people, bank owners, and
    accounts by owner; then propose the matches. Used once by scripts/build_subject_registry.py."""
    counts = {"people": 0, "health_people": 0, "bank_owners": 0, "accounts": 0}
    for person in session.exec(select(Person).where(Person.subject_id.is_(None))).all():
        person.subject_id = register_person(session, person.name).id
        session.add(person); counts["people"] += 1
    for person in session.exec(select(HealthPerson).where(HealthPerson.subject_id.is_(None),
                                                          HealthPerson.merged_into_id.is_(None))).all():
        person.subject_id = register_person(session, person.name).id
        session.add(person); counts["health_people"] += 1
    for connection in session.exec(select(BankConnection).where(BankConnection.owner_subject_id.is_(None))).all():
        subject = register_person(session, connection.owner)
        if subject is not None:
            connection.owner_subject_id = subject.id
            session.add(connection); counts["bank_owners"] += 1
    session.flush()
    counts["accounts"] = link_account_owners(session)
    counts["proposals"] = propose_matches(session)
    if commit:
        session.commit()
    return counts
