from app.models.merchant import Merchant
from app.models.transaction import TransactionType
from app.services.merchant_plan import apply_plan
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy, get_node
from tests.test_merchant_reconcile import _merchant, _t

PSI = "income.psi.sessions"
FUEL = "transport.car-running-costs.fuel"


def test_categories_follow_the_original_merchant_then_the_merge_and_rename_happen(session):
    ensure_taxonomy(session)
    client = _merchant(session, "Transfer to Bernardo Gaspar")
    client2 = _merchant(session, "Imed - Bernardo Filipe Gaspar")
    pay = _t(session, client, UNSORTED_SLUG, TransactionType.CREDIT); pay2 = _t(session, client2, UNSORTED_SLUG, TransactionType.CREDIT)
    atm_abroad = _merchant(session, "BCP ATM Withdrawal"); atm = _merchant(session, "ATM Withdrawal")
    abroad = _t(session, atm_abroad, "cash-giving.cash.atm-withdrawals"); home = _t(session, atm, "cash-giving.cash.atm-withdrawals")
    bank = _merchant(session, "Santander", confirmed=True); bank.by_provider = True; session.add(bank); session.commit()
    plan = {"cats": [{"id": client.id, "credit": PSI}, {"id": client2.id, "credit": PSI},
                     {"id": atm_abroad.id, "debit": "holidays-travel.holidays.on-trip-spending"}],
            "ops": [{"final": "Bernardo Filipe Gaspar", "ids": [client.id, client2.id], "explicit": True},
                    {"final": "Santander", "ids": [atm_abroad.id, atm.id], "explicit": True}]}
    dry = apply_plan(session, plan, dry_run=True)
    assert dry.category_entries_filed == 3 and dry.merchants_merged == 2
    report = apply_plan(session, plan)
    for t in (pay, pay2, abroad, home):
        session.refresh(t)
    assert pay.category_id == pay2.category_id == get_node(session, PSI).id
    assert abroad.category_id == get_node(session, "holidays-travel.holidays.on-trip-spending").id  # only the foreign one
    assert home.category_id == get_node(session, "cash-giving.cash.atm-withdrawals").id
    assert pay.merchant_id == pay2.merchant_id and abroad.merchant_id == home.merchant_id == bank.id
    survivor = session.get(Merchant, pay.merchant_id)
    assert survivor.canonical_name == "Bernardo Filipe Gaspar" and survivor.default_credit_category_id == get_node(session, PSI).id
    assert report.merchants_merged == 3
