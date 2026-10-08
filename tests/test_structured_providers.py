import pytest

from app.models.merchant import Merchant
from app.models.transaction import TransactionType
from app.services.merchant_relabel import relabel_structured
from app.services.structured_providers import ALL, LENDER, structured_merchant
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy, get_node
from tests.test_merchant_reconcile import _merchant, _t

# provider texts exactly as they sit in the live database
LOAN = ["COB.REC.31.000317997064096 (prestação crédito habitação)",
        "COB.REC.31.000317997056096/28 (Prestação Crédito Habitação)",
        "COB.REC.31.000317997064096/ 16 (Prestação Crédito Habitação)",
        "COB.REC.31.000302028859096/255", "Cob.Rec.31.000302028859096"]
INSURANCE = {"SEG:LAR 2023-07-26/2023-09-01": "seguro-multirriscos", "SEG VIDA 2026-09-02/2026-10-01": "seguro-vida",
             "SEG:EDF 2026-09-02/2026-10-01": "seguro-edificio", "SEG LAR -2026/08/21": "seguro-multirriscos"}


@pytest.mark.parametrize("text", LOAN)
def test_loan_collections_map_to_the_one_lender_merchant(text):
    assert structured_merchant(text) is LENDER


@pytest.mark.parametrize("text,fragment", INSURANCE.items())
def test_insurance_debits_map_to_their_fixed_merchants(text, fragment):
    assert fragment in structured_merchant(text).key


@pytest.mark.parametrize("text", ["SEGURANCA SOCIAL", "COB.REC.31", "COB.REC.31.123", "MODELO HIPER", "", None,
                                  "PAG.CTA.CART.31005126887780011", "DÉBITO DIRETO-REALVSEGUROS-91038975002"])
def test_everything_else_is_left_to_the_normal_path(text):
    assert structured_merchant(text) is None


def test_every_structured_merchant_targets_a_real_node(session):
    ensure_taxonomy(session)
    for spec in ALL:
        get_node(session, spec.node_slug)


def test_relabel_collapses_the_invented_merchants_and_leaves_categories_and_loan_links(session):
    ensure_taxonomy(session)
    mort = "loans-debt.loan-repayments.mortgage"
    junk = [_merchant(session, n) for n in ("Caixa Geral de Depósitos", "Banco do Brasil - Conta de Débito", "COB Electricity")]
    rows = [_t(session, m, mort, provider=p) for m, p in zip(junk, LOAN[:3])]
    for t in rows:
        t.debt_id = 1; session.add(t)
    edf = _merchant(session, "EDF")
    ins = _t(session, edf, "insurances.home.building-insurance-house-loan", provider="SEG:EDF 2026-09-02/2026-10-01")
    other = _t(session, junk[0], UNSORTED_SLUG, provider="TRANSFERENCIA XYZ")
    session.commit()

    dry = relabel_structured(session)
    assert dry.transactions_relabelled == 4 and rows[0].merchant_id == junk[0].id  # dry run writes nothing

    report = relabel_structured(session, dry_run=False)
    session.expire_all()
    assert report.transactions_relabelled == 4 and report.merchants_created == 2
    lender = session.query(Merchant).filter_by(normalized_key=LENDER.key).one()
    assert {t.merchant_id for t in rows} == {lender.id} and lender.canonical_name == "Santander – Crédito habitação"
    assert ins.merchant_id != edf.id
    assert all(t.debt_id == 1 for t in rows)
    assert rows[0].category_id == get_node(session, mort).id
    assert other.merchant_id == junk[0].id  # not a structured provider: untouched
    assert report.merchants_left_empty == 3  # Banco do Brasil, COB Electricity and EDF now have no entries
    assert relabel_structured(session, dry_run=False).transactions_relabelled == 0


@pytest.mark.asyncio
async def test_new_instalments_share_one_merchant_and_never_ask_the_model(session):
    from app.services.classification_engine import classify_transaction
    from tests.fakes.fake_gateway import FakeGateway

    ensure_taxonomy(session)
    gw = FakeGateway([])  # any gateway call raises
    base = _merchant(session, "placeholder")
    made = []
    for p in LOAN[:3]:
        t = _t(session, base, UNSORTED_SLUG, provider=p)
        t.merchant_id = None; session.add(t); session.commit()
        await classify_transaction(session, t, gateway=gw)
        made.append(t.merchant_id)
    assert gw.requests == [] and len(set(made)) == 1
    assert session.get(Merchant, made[0]).canonical_name == LENDER.name
