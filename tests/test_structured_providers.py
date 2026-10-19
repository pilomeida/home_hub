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
    rows = [_t(session, m, mort, description=p) for m, p in zip(junk, LOAN[:3])]
    for t in rows:
        t.debt_id = 1; session.add(t)
    edf = _merchant(session, "EDF")
    ins = _t(session, edf, "insurances.home.building-insurance-house-loan", description="SEG:EDF 2026-09-02/2026-10-01")
    other = _t(session, junk[0], UNSORTED_SLUG, description="TRANSFERENCIA XYZ")
    session.commit()

    dry = relabel_structured(session)
    assert dry.transactions_relabelled == 4 and rows[0].merchant_id == junk[0].id  # dry run writes nothing

    report = relabel_structured(session, dry_run=False)
    session.expire_all()
    assert report.transactions_relabelled == 4 and report.merchants_created == 2
    lender = session.query(Merchant).filter_by(normalized_key=LENDER.key).one()
    assert {t.merchant_id for t in rows} == {lender.id} and lender.canonical_name == "Santander" and lender.by_provider is True
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
        t = _t(session, base, UNSORTED_SLUG, description=p)
        t.merchant_id = None; session.add(t); session.commit()
        await classify_transaction(session, t, gateway=gw)
        made.append(t.merchant_id)
    assert gw.requests == [] and len(set(made)) == 1
    assert session.get(Merchant, made[0]).canonical_name == LENDER.name


def test_the_three_insurance_kinds_share_one_counterparty_merchant(session):
    from app.services.merchant_relabel import ensure_structured_merchant
    from app.services.structured_providers import structured_merchant
    ensure_taxonomy(session)
    merchants = {ensure_structured_merchant(session, structured_merchant(p))[0].id
                 for p in ("SEG:LAR 2026-10-02/2026-11-01", "SEG VIDA 15.268206-2026/09/21", "SEG:EDF 2026-10-02/2026-11-01")}
    assert len(merchants) == 1
    survivor = session.get(Merchant, merchants.pop())
    assert survivor.canonical_name == "Aegon Santander" and survivor.by_provider is True
    assert session.query(Merchant).filter(Merchant.merged_into_id == survivor.id).count() == 2  # two aliases


# --- Multibanco payment entities (descriptions as the bank prints them) ---

def test_a_multibanco_payment_names_its_payee_by_entity_not_by_the_text_around_it():
    from app.services.payment_entities import entity_of
    for text, entity in (("PAG SERVICOS  1835 27134-627088996", "27134"),
                         ("PAG SERVICOS *1835 12442-540006308 AMIGO*EASYPAY.P", "12442"),
                         ("AMIGO*EASYPAY.P 12442-540006308", "12442"),
                         ("PAG SERVICOS 20160-402818490", "20160")):
        assert entity_of(text).number == entity
    assert entity_of("PAG SERVICOS 1835 99999-123456789") is None      # an entity nobody has named yet
    assert entity_of("7303599-00-PAG.CTA.CARTAO") is None
    assert structured_merchant("PAG SERVICOS 23332-200645166").name == "Câmara Municipal de Mafra"
    assert structured_merchant("PAG SERVICOS 23332-200645166").key == "entity:23332"


def test_refile_by_entity_moves_a_mistaken_electricity_entry_and_spares_a_checked_one(session):
    from datetime import date
    from app.models.domain import Domain
    from app.models.transaction import Transaction, TransactionType
    from app.services.payment_entities import refile_by_entity
    from app.services.taxonomy import ensure_taxonomy, file_transaction, get_node
    from tests.knowledge_factories import make_document
    ensure_taxonomy(session)
    doc = make_document(session, domain=Domain.FINANCIALS, category="statement", filename="s.pdf")
    wrong, checked, other = (Transaction(document_id=doc.id, description=d, amount=a, transaction_type=TransactionType.DEBIT,
                                         paid_date=date(2026, 9, 1), checked_by=c)
                             for d, a, c in (("PAG SERVICOS 1835 21056-111111111", 100.0, None),
                                             ("PAG SERVICOS 1835 21056-222222222", 100.0, "Pedro"),
                                             ("PAG SERVICOS 1835 99999-333333333", 5.0, None)))
    session.add_all([wrong, checked, other])
    session.flush()
    electricity = get_node(session, "housing.utilities.electricity")
    for t in (wrong, checked, other):
        file_transaction(session, t, electricity)
    session.commit()
    assert [t.id for t, _ in refile_by_entity(session, dry_run=True)] == [wrong.id]
    refile_by_entity(session, dry_run=False)
    session.refresh(wrong); session.refresh(checked); session.refresh(other)
    assert wrong.category_id == get_node(session, "taxes-financial-costs.taxes.social-security").id
    assert wrong.merchant_id is not None
    assert checked.category_id == other.category_id == electricity.id
