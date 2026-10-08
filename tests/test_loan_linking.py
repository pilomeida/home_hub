"""Automatic loan linking. All loan numbers are synthetic."""

import json

import pytest
from sqlmodel import select

from app.models.debt import Debt, DebtDirection, DebtKind
from app.models.document import Document, DocumentSource
from app.models.merchant import Merchant
from app.models.transaction import Category, Nature, Transaction, TransactionType
from app.services.classification_engine import classify_transaction
from app.services.loan_linking import digits_only, find_loan_for, link_all_unlinked, link_transaction_to_loan
from app.services.taxonomy import ensure_taxonomy, get_node
from tests.fakes.fake_gateway import FakeGateway

MORTGAGE_NO = "000100000000001"
PERSONAL_NO = "000100000000002"


def _debt(session, number, loan_type="mortgage"):
    d = Debt(kind=DebtKind.FORMAL, direction=DebtDirection.OWED_BY_US, original_amount=1000.0,
             current_balance=900, name=f"Loan {number[-4:]}", external_number=number, loan_type=loan_type)
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def _doc(session):
    d = Document(filename="s.pdf", file_path="/tmp/s.pdf", content_hash="h-loan-link", source=DocumentSource.MANUAL)
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def _txn(session, provider, ttype=TransactionType.DEBIT):
    doc = session.exec(select(Document)).first() or _doc(session)
    t = Transaction(document_id=doc.id, provider=provider, amount=100.0, transaction_type=ttype)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


@pytest.fixture()
def tree(session):
    ensure_taxonomy(session)


def test_digits_only():
    assert digits_only("COB.REC.31.000100000000001/ 35") == "31000100000000001" + "35"


def test_slugs_exist(session, tree):
    get_node(session, "loans-debt.loan-repayments.mortgage")
    get_node(session, "loans-debt.loan-repayments.personal-loans")


def test_debit_links_and_files_under_mortgage(session, tree):
    debt = _debt(session, MORTGAGE_NO)
    t = _txn(session, f"COB.REC.31.{MORTGAGE_NO}/ 35")
    assert link_transaction_to_loan(session, t) is True
    session.commit()
    session.refresh(t)
    node = get_node(session, "loans-debt.loan-repayments.mortgage")
    assert t.debt_id == debt.id and t.category_id == node.id and t.debt_candidate_reviewed is True
    from app.services.taxonomy import legacy_category_for
    assert t.category == legacy_category_for(session, node)
    session.refresh(debt)
    assert float(debt.current_balance) == 900.0


def test_personal_loan_files_under_personal(session, tree):
    _debt(session, PERSONAL_NO, "personal")
    t = _txn(session, f"Cob.Rec.31.{PERSONAL_NO}")
    assert link_transaction_to_loan(session, t)
    assert t.category_id == get_node(session, "loans-debt.loan-repayments.personal-loans").id


def test_other_loan_type_files_under_personal(session, tree):
    _debt(session, PERSONAL_NO, "other")
    t = _txn(session, f"Cob.Rec.31.{PERSONAL_NO}")
    assert link_transaction_to_loan(session, t)
    assert t.category_id == get_node(session, "loans-debt.loan-repayments.personal-loans").id


def test_credit_and_transfer_never_link(session, tree):
    _debt(session, MORTGAGE_NO)
    for tt in (TransactionType.CREDIT, TransactionType.TRANSFER):
        t = _txn(session, f"COB.REC.31.{MORTGAGE_NO}/ 35", tt)
        assert link_transaction_to_loan(session, t) is False
        assert t.debt_id is None


def test_ambiguity_no_link(session, tree):
    _debt(session, "9001002001")
    _debt(session, "001002001")  # substring of the first's run
    t = _txn(session, "COB.REC.31.9001002001")
    assert find_loan_for(session, t.provider) is None
    assert link_transaction_to_loan(session, t) is False


def test_separator_collision_does_not_link(session, tree):
    _debt(session, "123456789")
    # digits only match when separators are merged
    t = _txn(session, "REF 12345.6789 X")
    assert find_loan_for(session, t.provider) is None
    assert link_transaction_to_loan(session, t) is False


def test_already_linked_elsewhere_untouched(session, tree):
    other = _debt(session, "555000111")
    _debt(session, MORTGAGE_NO)
    t = _txn(session, f"COB.REC.31.{MORTGAGE_NO}")
    t.debt_id = other.id
    session.add(t)
    session.commit()
    assert link_transaction_to_loan(session, t) is False
    assert t.debt_id == other.id


def test_idempotent(session, tree):
    _debt(session, MORTGAGE_NO)
    t = _txn(session, f"COB.REC.31.{MORTGAGE_NO}")
    assert link_transaction_to_loan(session, t) is True
    assert link_transaction_to_loan(session, t) is False


def test_link_all_unlinked(session, tree):
    _debt(session, MORTGAGE_NO)
    for i in range(3):
        _txn(session, f"COB.REC.31.{MORTGAGE_NO}/ {i}")
    _txn(session, "MODELO HIPER")
    assert link_all_unlinked(session) == 3
    assert link_all_unlinked(session) == 0


def _gw(slug="food.groceries.supermarket"):
    return FakeGateway([{"text": json.dumps({"canonical_name": "X", "node_slug": slug, "nature": "essential"}),
                         "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}])


@pytest.mark.asyncio
async def test_classify_links_new_instalment_and_keeps_loan_filing(session, tree):
    debt = _debt(session, MORTGAGE_NO)
    t = _txn(session, f"COB.REC.31.{MORTGAGE_NO}/ 35")
    # LLM would file it under groceries; the loan link must win
    await classify_transaction(session, t, gateway=_gw())
    session.commit()
    session.refresh(t)
    assert t.debt_id == debt.id
    assert t.category_id == get_node(session, "loans-debt.loan-repayments.mortgage").id


@pytest.mark.asyncio
async def test_classify_does_not_refile_with_existing_merchant_memory(session, tree):
    debt = _debt(session, MORTGAGE_NO)
    prov = f"COB.REC.31.{MORTGAGE_NO}/ 35"
    from app.services.classification_engine import normalize_provider
    m = Merchant(canonical_name="Bank", default_category=Category.GROCERIES, normalized_key=normalize_provider(prov),
                 default_category_id=get_node(session, "food.groceries.supermarket").id,
                 default_nature=Nature.ESSENTIAL)
    session.add(m)
    session.commit()
    t = _txn(session, prov)
    await classify_transaction(session, t, gateway=_gw())
    session.commit()
    assert t.debt_id == debt.id
    assert t.category_id == get_node(session, "loans-debt.loan-repayments.mortgage").id


@pytest.mark.asyncio
async def test_classify_skips_when_debt_already_set(session, tree):
    other = _debt(session, "555000111")
    _debt(session, MORTGAGE_NO)
    t = _txn(session, f"COB.REC.31.{MORTGAGE_NO}")
    t.debt_id = other.id
    session.add(t)
    session.commit()
    await classify_transaction(session, t, gateway=_gw())
    assert t.debt_id == other.id


@pytest.mark.asyncio
async def test_positions_document_back_links_existing_instalment(session, tmp_path):
    from tests.test_position_store import _doc as pdoc, _STATEMENT_JSON, _reply
    from app.services.position_store import process_positions_document
    ensure_taxonomy(session)
    t = _txn(session, "COB.REC.31.900100200/ 37")
    ext = await process_positions_document(
        session, pdoc(session, tmp_path), gateway=FakeGateway([_reply(json.dumps(_STATEMENT_JSON))]))
    assert ext.status == "ok"
    session.refresh(t)
    debt = session.exec(select(Debt).where(Debt.external_number == "900100200")).one()
    assert t.debt_id == debt.id


@pytest.mark.asyncio
async def test_linking_failure_never_fails_document(session, tmp_path, monkeypatch):
    from tests.test_position_store import _doc as pdoc, _STATEMENT_JSON, _reply
    from app.services import position_store
    from app.services.position_store import process_positions_document

    def boom(session):
        raise RuntimeError("link failed")
    monkeypatch.setattr(position_store, "link_all_unlinked", boom)
    ext = await process_positions_document(
        session, pdoc(session, tmp_path), gateway=FakeGateway([_reply(json.dumps(_STATEMENT_JSON))]))
    assert ext.status == "ok"


@pytest.mark.asyncio
async def test_linking_failure_in_a_dirty_session_still_leaves_extraction_ok(session, tmp_path, monkeypatch):
    """M3: a back-link that fails mid-flush must be rolled back, or the next commit raises."""
    from tests.test_position_store import _doc as pdoc, _STATEMENT_JSON, _reply
    from app.models.position import DebtMatchRule, PositionExtraction
    from app.services import position_store
    from app.services.position_store import process_positions_document
    doc = pdoc(session, tmp_path)

    def boom(s):
        s.add(DebtMatchRule(debt_id=1, normalized_key="k"))
        s.add(DebtMatchRule(debt_id=1, normalized_key="k"))  # unique violation on flush
        s.flush()
    monkeypatch.setattr(position_store, "link_all_unlinked", boom)
    ext = await process_positions_document(session, doc, gateway=FakeGateway([_reply(json.dumps(_STATEMENT_JSON))]))
    assert ext.status == "ok"
    session.expire_all()
    assert session.exec(select(PositionExtraction)).one().status == "ok"


def test_link_all_unlinked_loads_the_loans_once(session, tree):
    from sqlalchemy import event
    _debt(session, MORTGAGE_NO)
    _debt(session, PERSONAL_NO, "personal")
    ts = [_txn(session, f"COB.REC.31.{MORTGAGE_NO}/ {i}") for i in range(5)] + [_txn(session, "SOME SHOP")]
    seen = []
    engine = session.get_bind()

    def count(conn, cursor, statement, *a):
        if "FROM debts" in statement:
            seen.append(statement)
    event.listen(engine, "before_cursor_execute", count)
    try:
        assert link_all_unlinked(session) == 5
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(seen) == 1


# ---- Block C: insurance debits wired into classification and back-linking ----

def _movement(session, debt, life=0.0, building=0.0):
    from datetime import date
    from app.models.position import LoanMovement
    session.add(LoanMovement(debt_id=debt.id, instalment_number=1, movement_date=date(2026, 9, 2), capital=1.0,
                             interest=1.0, insurance=life + building, insurance_life=life,
                             insurance_building=building, document_id=_doc(session).id))
    session.commit()


@pytest.mark.asyncio
async def test_classify_links_an_insurance_debit_and_files_it_under_the_insurance_node(session, tree):
    from datetime import date
    debt = _debt(session, MORTGAGE_NO)
    _movement(session, debt, building=20.0)
    t = _txn(session, "SEG:EDF 2026-09-02/2026-10-01")
    t.amount, t.paid_date = 20.0, date(2026, 9, 3)
    session.add(t)
    session.commit()
    # the LLM would file it under groceries; the insurance link must win
    await classify_transaction(session, t, gateway=_gw())
    session.commit()
    session.refresh(t)
    assert t.debt_id == debt.id
    assert t.category_id == get_node(session, "insurances.home.building-insurance-house-loan").id


@pytest.mark.asyncio
async def test_classify_insurance_failure_is_non_fatal(session, tree, monkeypatch):
    from app.services import classification_engine
    _debt(session, MORTGAGE_NO)
    t = _txn(session, "SEG:EDF 2026-09-02/2026-10-01")

    def boom(*a, **k):
        raise RuntimeError("insurance exploded")
    monkeypatch.setattr(classification_engine, "link_insurance_transaction", boom)
    await classify_transaction(session, t, gateway=_gw())
    session.commit()
    assert t.debt_id is None and t.merchant_id is not None


@pytest.mark.asyncio
async def test_classify_instalment_link_comes_first_and_skips_insurance(session, tree, monkeypatch):
    from app.services import classification_engine
    _debt(session, MORTGAGE_NO)
    calls = []
    monkeypatch.setattr(classification_engine, "link_insurance_transaction", lambda *a, **k: calls.append(1) or False)
    t = _txn(session, f"COB.REC.31.{MORTGAGE_NO}/ 35")
    await classify_transaction(session, t, gateway=_gw())
    assert calls == [] and t.debt_id is not None


@pytest.mark.asyncio
async def test_positions_document_back_links_insurance_debits(session, tmp_path):
    from datetime import date
    from tests.test_position_store import _doc as pdoc, _STATEMENT_JSON, _reply
    from app.services.position_store import process_positions_document
    ensure_taxonomy(session)
    stmt = json.loads(json.dumps(_STATEMENT_JSON))
    stmt["loans"][0]["rows"].append({"date": "2026-07-02", "instalment_number": 37, "component": "insurance_life",
                                     "amount": 5.5, "balance_after": None})
    t = _txn(session, "SEG VIDA 15.000001-2026/08/21")
    t.amount, t.paid_date = 5.5, date(2026, 7, 3)
    session.add(t)
    session.commit()
    ext = await process_positions_document(session, pdoc(session, tmp_path), gateway=FakeGateway([_reply(json.dumps(stmt))]))
    assert ext.status == "ok", ext.error
    session.refresh(t)
    debt = session.exec(select(Debt).where(Debt.external_number == "900100200")).one()
    assert t.debt_id == debt.id and t.category_id == get_node(session, "insurances.home.life-insurance-house-loan").id
