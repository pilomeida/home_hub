import json

import pytest

from app.models.document import Document, DocumentSource
from app.models.merchant import Merchant
from app.models.transaction import Category, Transaction, TransactionType
from app.services.merchant_reconcile import reconcile_merchants
from app.services.merchant_rules import keyword_node_slug
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy, file_transaction, get_node, is_refund

SM, FUEL, REST = "food.groceries.supermarket", "transport.car-running-costs.fuel", "food.eat-out.restaurants"
ATM = "cash-giving.cash.atm-withdrawals"


def _merchant(session, name, node=None, confirmed=False):
    m = Merchant(canonical_name=name, normalized_key=name.lower(), confirmed=confirmed,
                 default_category_id=get_node(session, node).id if node else None)
    session.add(m); session.commit(); session.refresh(m)
    return m


_n = [0]


def _t(session, m, slug, ttype=TransactionType.DEBIT, amount=10.0, provider=None, debt_id=None):
    _n[0] += 1
    doc = Document(filename=f"d{_n[0]}.pdf", file_path=f"/tmp/d{_n[0]}.pdf",
                   content_hash=f"hash-{_n[0]}", source=DocumentSource.MANUAL)
    session.add(doc); session.commit()
    t = Transaction(document_id=doc.id, provider=provider or m.canonical_name, category=Category.OTHER,
                    transaction_type=ttype, amount=amount, merchant_id=m.id, debt_id=debt_id)
    session.add(t); session.commit()
    file_transaction(session, t, get_node(session, slug))
    session.commit(); session.refresh(t)
    return t


@pytest.mark.parametrize("text", ["Modelo Hiper", "MO MODELO CONTINENTE", "LIDL & CO", "ALDI Sul", "Pingo  Doce",
                                  "Intermarché", "INTERMARCHE", "CCR-MODELO HIPER reembolso"])
def test_keyword_rule_matches_the_chains(text):
    assert keyword_node_slug(text) == SM


@pytest.mark.parametrize("text", ["Aldina Cabeleireira", "Banco Modelos", "Valdivia", "Continentes Lda", None, ""])
def test_keyword_rule_ignores_lookalikes(text):
    assert keyword_node_slug(text) is None


def test_is_refund_only_for_a_credit_under_a_spending_node(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Shop")
    credit = _t(session, m, SM, TransactionType.CREDIT)
    debit = _t(session, m, SM)
    assert is_refund(credit, get_node(session, SM)) and not is_refund(debit, get_node(session, SM))
    assert not is_refund(credit, get_node(session, UNSORTED_SLUG)) and not is_refund(credit, None)


def test_supermarket_rule_overrides_every_other_filing_even_a_confirmed_merchant(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Modelo Hiper", ATM, confirmed=True)
    a, b = _t(session, m, ATM, provider="MO 2640-000 MAFR"), _t(session, m, SM)
    refund = _t(session, m, UNSORTED_SLUG, TransactionType.CREDIT, provider="CCR-MODELO HIPER reembolso")
    report = reconcile_merchants(session, dry_run=False)
    for t in (a, b, refund):
        session.refresh(t)
        assert t.category_id == get_node(session, SM).id
    session.refresh(m)
    assert m.default_category_id == get_node(session, SM).id and m.confirmed is True
    assert report.transactions_moved == 2 and report.merchants_changed == 1


def test_plurality_of_debits_wins_and_credits_follow_as_refunds(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Padaria X", REST)
    d1, d2, d3 = _t(session, m, SM), _t(session, m, SM), _t(session, m, REST)
    refund = _t(session, m, UNSORTED_SLUG, TransactionType.CREDIT)
    reconcile_merchants(session, dry_run=False)
    for t in (d1, d2, d3, refund):
        session.refresh(t)
        assert t.category_id == get_node(session, SM).id
    session.refresh(m)
    assert m.default_category_id == get_node(session, SM).id


def test_confirmed_merchant_default_beats_plurality(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Posto Y", FUEL, confirmed=True)
    rows = [_t(session, m, SM), _t(session, m, SM), _t(session, m, UNSORTED_SLUG)]
    reconcile_merchants(session, dry_run=False)
    for t in rows:
        session.refresh(t)
        assert t.category_id == get_node(session, FUEL).id


def test_credit_cannot_follow_a_cash_or_loan_node_and_stays_put(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Matias", ATM, confirmed=True)
    out = _t(session, m, UNSORTED_SLUG)
    back = _t(session, m, UNSORTED_SLUG, TransactionType.CREDIT)
    report = reconcile_merchants(session, dry_run=False)
    session.refresh(out); session.refresh(back)
    assert out.category_id == get_node(session, ATM).id
    assert back.category_id == get_node(session, UNSORTED_SLUG).id and report.credits_left == 1


def test_loan_linked_rows_and_transfers_are_never_moved(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Banco", FUEL, confirmed=True)
    loan = _t(session, m, "loans-debt.loan-repayments.car-loan", debt_id=None)
    loan.debt_id = 1; session.add(loan)
    transfer = _t(session, m, UNSORTED_SLUG, TransactionType.TRANSFER)
    session.commit()
    before = (loan.category_id, transfer.category_id)
    reconcile_merchants(session, dry_run=False)
    session.refresh(loan); session.refresh(transfer)
    assert (loan.category_id, transfer.category_id) == before


def test_dry_run_changes_nothing_but_reports_and_logs_every_move(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Continente", ATM)
    t = _t(session, m, ATM)
    report = reconcile_merchants(session)
    session.refresh(t); session.refresh(m)
    assert t.category_id == get_node(session, ATM).id and m.default_category_id == get_node(session, ATM).id
    assert report.moves == [(t.id, get_node(session, ATM).id, get_node(session, SM).id)]
    json.dumps(report.moves)  # loggable for reversal


def test_reconcile_is_idempotent(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Lidl")
    _t(session, m, REST)
    reconcile_merchants(session, dry_run=False)
    again = reconcile_merchants(session, dry_run=False)
    assert (again.transactions_moved, again.merchants_changed) == (0, 0)


def test_merchant_with_only_unsorted_rows_and_no_default_is_left_unresolved(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Mystery")
    t = _t(session, m, UNSORTED_SLUG)
    report = reconcile_merchants(session, dry_run=False)
    session.refresh(t)
    assert t.category_id == get_node(session, UNSORTED_SLUG).id and report.merchants_unresolved == 1


@pytest.mark.asyncio
async def test_new_chain_merchant_is_filed_by_the_rule_without_asking_the_llm(session):
    from app.services.classification_engine import classify_transaction
    from tests.fakes.fake_gateway import FakeGateway

    ensure_taxonomy(session)
    m = _merchant(session, "placeholder")
    t = _t(session, m, UNSORTED_SLUG, provider="PINGO DOCE 1234 LISBOA")
    t.merchant_id = None; session.add(t); session.commit()
    gw = FakeGateway([])  # any gateway call raises
    await classify_transaction(session, t, gateway=gw)
    assert gw.requests == [] and t.category_id == get_node(session, SM).id


def test_credit_only_payer_is_not_turned_into_a_refund(session):
    ensure_taxonomy(session)
    client = _merchant(session, "Some Client", "family.personal.general-shopping")
    paid = _t(session, client, UNSORTED_SLUG, TransactionType.CREDIT, amount=1500.0)
    report = reconcile_merchants(session, dry_run=False)
    session.refresh(paid)
    assert paid.category_id == get_node(session, UNSORTED_SLUG).id and report.credits_left == 1


@pytest.mark.asyncio
async def test_merchant_memory_leaves_a_credit_of_a_never_paid_merchant_for_review(session):
    from app.services.classification_engine import classify_transaction
    from tests.fakes.fake_gateway import FakeGateway

    ensure_taxonomy(session)
    client = _merchant(session, "Some Client", "family.personal.general-shopping")
    t = _t(session, client, UNSORTED_SLUG, TransactionType.CREDIT, provider="Some Client")
    t.category_id = None; session.add(t); session.commit()
    await classify_transaction(session, t, gateway=FakeGateway([]))
    assert t.category_id is None
