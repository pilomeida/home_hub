import pytest

from app.models.merchant import Merchant
from app.models.transaction import TransactionType
from app.services.classification_engine import classify_transaction, normalize_provider
from app.services.merchant_assign import assign_category
from app.services.merchant_reconcile import reconcile_merchants
from app.services.provider_rules import apply_to_providers, provider_key
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy, get_node, looks_like_person_transfer
from app.services.transfer_direction import direction_from_description, recover_transfer_directions
from tests.fakes.fake_gateway import FakeGateway
from tests.test_merchant_reconcile import _merchant, _t

TAX = "taxes-financial-costs.taxes.social-security"
BENEFIT = "income.benefits-refunds-from-the-state.family-or-other-benefits"
PSI = "income.psi.sessions"


def test_assign_an_inflow_category_sets_the_credit_default_and_leaves_the_outflow_one(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Segurança Social", TAX, confirmed=True)
    debit = _t(session, m, UNSORTED_SLUG); credit = _t(session, m, UNSORTED_SLUG, TransactionType.CREDIT)
    assign_category(session, [m.id], get_node(session, BENEFIT), refile_existing=True)
    session.refresh(m); session.refresh(debit); session.refresh(credit)
    assert m.default_category_id == get_node(session, TAX).id and m.default_credit_category_id == get_node(session, BENEFIT).id
    assert credit.category_id == get_node(session, BENEFIT).id and debit.category_id == get_node(session, UNSORTED_SLUG).id


@pytest.mark.asyncio
async def test_a_new_entry_takes_the_default_of_its_own_direction(session):
    ensure_taxonomy(session)
    provider = "SEGURANCA SOCIAL"
    m = Merchant(canonical_name="Segurança Social", normalized_key=normalize_provider(provider), confirmed=True,
                 default_category_id=get_node(session, TAX).id, default_credit_category_id=get_node(session, BENEFIT).id)
    session.add(m); session.commit()
    out = _t(session, m, UNSORTED_SLUG, provider=provider); inn = _t(session, m, UNSORTED_SLUG, TransactionType.CREDIT, provider=provider)
    for t in (out, inn):
        t.category_id = None; session.add(t)
    session.commit()
    await classify_transaction(session, out, gateway=FakeGateway([])); await classify_transaction(session, inn, gateway=FakeGateway([]))
    assert out.category_id == get_node(session, TAX).id and inn.category_id == get_node(session, BENEFIT).id


def test_reconcile_files_a_clients_credits_as_income_and_keeps_the_merchants_other_side(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Cliente Exemplo", "transport.car-running-costs.fuel")
    m.default_credit_category_id = get_node(session, PSI).id; session.add(m); session.commit()
    pay = _t(session, m, UNSORTED_SLUG, TransactionType.CREDIT)
    reconcile_merchants(session, dry_run=False)
    session.refresh(pay)
    assert pay.category_id == get_node(session, PSI).id


def test_a_persons_transfer_to_you_is_not_a_shop_refund(session):
    ensure_taxonomy(session)
    shop = _merchant(session, "Zara", "family.personal.clothing")
    _t(session, shop, "family.personal.clothing")  # a purchase
    refund = _t(session, shop, UNSORTED_SLUG, TransactionType.CREDIT, provider="CCR-ZARA refund")
    person = _merchant(session, "Ivo Ferreira", "family.personal.general-shopping")
    _t(session, person, "family.personal.general-shopping")  # you paid him once
    transfer = _t(session, person, UNSORTED_SLUG, TransactionType.CREDIT, provider="TRF.IMED. DE IVO ANDRE SERAFIM FERREIRA-R7544210")
    from app.services.taxonomy import auto_fits
    assert auto_fits(session, refund, get_node(session, "family.personal.clothing"))
    assert not auto_fits(session, transfer, get_node(session, "family.personal.general-shopping"))
    assert looks_like_person_transfer(transfer.provider) and not looks_like_person_transfer("CCR-ZARA refund")


@pytest.mark.parametrize("text,way", [
    ("TRF.IMED. DE IVO ANDRE SERAFIM FERREIRA-R7544210", "in"), ("TRF CRED INTRABANC DE RUTE ALEXANDRA DA S-23940057", "in"),
    ("Transfer from MATIAS ALMEIDA - Loan repayment", "in"), ("TRF.IMED. P/ MARCO MOREIRAS", "out"),
    ("TRF CRED INTRABANC P/ RUTE ALEXANDRA DA S", "out"), ("Transfer to MATIAS ALMEIDA", "out"), ("PARA OBRA-25473193", "out"),
    ("TRANSF-24206738", None), ("INTER-27504056", None), ("COMPRA 3315 CONTINENTE", None)])
def test_the_banks_own_words_give_the_direction(text, way):
    assert direction_from_description(text) == way


def test_transfer_entries_get_a_direction_unless_internal_or_unclear(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Pessoa")
    out = _t(session, m, UNSORTED_SLUG, TransactionType.TRANSFER, provider="TRF.IMED. P/ MARCO MOREIRAS")
    inn = _t(session, m, UNSORTED_SLUG, TransactionType.TRANSFER, provider="TRF.IMED. DE IVO ANDRE")
    vague = _t(session, m, UNSORTED_SLUG, TransactionType.TRANSFER, provider="TRANSF-24206738")
    own = _t(session, m, "internal-transfers.between-my-accounts.santander-revolut", TransactionType.TRANSFER, provider="TRF P/ REVOLUT")
    dry = recover_transfer_directions(session)
    assert (dry.to_debit, dry.to_credit, dry.unclear) == (1, 1, 1)
    recover_transfer_directions(session, dry_run=False)
    for t in (out, inn, vague, own):
        session.refresh(t)
    assert (out.transaction_type, inn.transaction_type) == (TransactionType.DEBIT, TransactionType.CREDIT)
    assert vague.transaction_type == TransactionType.TRANSFER and own.transaction_type == TransactionType.TRANSFER


def test_a_human_can_file_a_reversal_credit_under_the_loan(session):
    ensure_taxonomy(session)
    bank = _merchant(session, "Santander")
    rev = _t(session, bank, UNSORTED_SLUG, TransactionType.CREDIT, provider="ESTORNO 31.0003.17997064096")
    rep = apply_to_providers(session, [provider_key("ESTORNO 31.0003.17997064096")], get_node(session, "loans-debt.loan-repayments.mortgage"))
    session.refresh(rev)
    assert rev.category_id == get_node(session, "loans-debt.loan-repayments.mortgage").id and rep.left_alone == 0


def test_the_new_leaves_exist(session):
    ensure_taxonomy(session)
    assert get_node(session, "psi-expenses.premises.session-room-rental").kind == "out"
    assert get_node(session, "internal-transfers.between-my-accounts.cash-paid-into-the-account").kind == "neutral"


def test_a_category_instruction_is_applied_per_direction_and_sets_the_matching_default(session):
    from app.services.merchant_categories import apply_merchant_category
    ensure_taxonomy(session)
    m = _merchant(session, "Segurança Social")
    out = _t(session, m, UNSORTED_SLUG); inn = _t(session, m, UNSORTED_SLUG, TransactionType.CREDIT)
    loan = _t(session, m, UNSORTED_SLUG); loan.debt_id = 1; session.add(loan); session.commit()
    dry = apply_merchant_category(session, m.id, BENEFIT, TAX, dry_run=True)
    assert dry.filed == 2
    apply_merchant_category(session, m.id, BENEFIT, TAX)
    for t in (out, inn, loan):
        session.refresh(t)
    session.refresh(m)
    assert out.category_id == get_node(session, TAX).id and inn.category_id == get_node(session, BENEFIT).id
    assert loan.category_id == get_node(session, UNSORTED_SLUG).id  # tied to a loan: never moved
    assert (m.default_category_id, m.default_credit_category_id) == (get_node(session, TAX).id, get_node(session, BENEFIT).id)
