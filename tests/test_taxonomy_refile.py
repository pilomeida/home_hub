import pytest
from app.models.document import Document, DocumentSource
from app.models.merchant import Merchant
from app.models.transaction import Category, Transaction, TransactionType
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy, file_transaction, flow_of, get_node
from app.services.taxonomy_refile import refile_all


def _txn(session, provider, category, ttype=TransactionType.DEBIT, merchant_id=None):
    doc = Document(filename=f"{provider}.pdf", file_path=f"/tmp/{provider}.pdf",
                   content_hash=f"h-{provider}", source=DocumentSource.MANUAL)
    session.add(doc); session.commit()
    t = Transaction(document_id=doc.id, provider=provider, category=category,
                    transaction_type=ttype, amount=10.0, merchant_id=merchant_id)
    session.add(t); session.commit(); session.refresh(t)
    return t


def test_file_transaction_dual_writes_legacy_category(session):
    ensure_taxonomy(session)
    t = _txn(session, "EDP", Category.OTHER)
    file_transaction(session, t, get_node(session, "housing.utilities.electricity"))
    assert t.category_id == get_node(session, "housing.utilities.electricity").id
    assert t.category == Category.ELECTRICITY


def test_file_transaction_under_unsorted_keeps_legacy_category(session):
    ensure_taxonomy(session)
    t = _txn(session, "SALARY", Category.INCOME, TransactionType.CREDIT)
    unsorted = get_node(session, UNSORTED_SLUG)
    file_transaction(session, t, unsorted)
    assert t.category_id == unsorted.id
    assert t.category == Category.INCOME


def test_flow_of_uses_kind_and_falls_back_to_type_for_unsorted(session):
    ensure_taxonomy(session)
    out_t = _txn(session, "A", Category.GROCERIES)
    assert flow_of(out_t, get_node(session, "food.groceries.supermarket")) == "out"
    assert flow_of(out_t, get_node(session, "income.psi.sessions")) == "in"
    assert flow_of(out_t, get_node(session, "internal-transfers.between-my-accounts.santander-card")) == "neutral"
    credit = _txn(session, "B", Category.OTHER, TransactionType.CREDIT)
    assert flow_of(credit, get_node(session, UNSORTED_SLUG)) == "in"
    assert flow_of(out_t, get_node(session, UNSORTED_SLUG)) == "out"


def test_refile_prefers_confirmed_merchant_then_legacy_then_review(session):
    ensure_taxonomy(session)
    fuel = get_node(session, "transport.car-running.fuel")
    m = Merchant(canonical_name="Galp", normalized_key="galp", confirmed=True, default_category_id=fuel.id)
    session.add(m); session.commit()
    from_merchant = _txn(session, "GALP", Category.OTHER_EXPENSE, merchant_id=m.id)
    from_legacy = _txn(session, "CONTINENTE", Category.GROCERIES)
    ambiguous = _txn(session, "MYSTERY", Category.OTHER_EXPENSE)

    report = refile_all(session, dry_run=False)

    assert from_merchant.category_id == fuel.id
    assert from_legacy.category_id == get_node(session, "food.groceries.supermarket").id
    assert ambiguous.category_id == get_node(session, UNSORTED_SLUG).id
    assert (report.filed_from_merchant, report.filed_from_legacy, report.sent_to_review) == (1, 1, 1)


def test_refile_dry_run_changes_nothing_and_rerun_skips_filed(session):
    ensure_taxonomy(session)
    t = _txn(session, "CONTINENTE", Category.GROCERIES)
    report = refile_all(session, dry_run=True)
    session.refresh(t)
    assert t.category_id is None and report.filed_from_legacy == 1
    refile_all(session, dry_run=False)
    assert refile_all(session, dry_run=False).already_filed == 1


@pytest.mark.asyncio
async def test_reclassify_unsorted_costs_one_llm_call_per_merchant(session):
    import json
    from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
    from app.services.taxonomy_refile import reclassify_unsorted

    ensure_taxonomy(session)
    unsorted = get_node(session, UNSORTED_SLUG)
    m = Merchant(canonical_name="Galp", normalized_key="galp", confirmed=False)
    mystery = Merchant(canonical_name="Mystery", normalized_key="mystery")
    session.add(m); session.add(mystery); session.commit()
    t1, t2 = _txn(session, "GALP 1", Category.OTHER, merchant_id=m.id), _txn(session, "GALP 2", Category.OTHER, merchant_id=m.id)
    t3 = _txn(session, "MYSTERY", Category.OTHER, merchant_id=mystery.id)
    for t in (t1, t2, t3):
        file_transaction(session, t, unsorted)
    session.commit()
    gw = FakeGateway([
        gateway_text_result(json.dumps({"canonical_name": "Galp", "node_slug": "transport.car-running.fuel", "nature": "essential"})),
        gateway_text_result(json.dumps({"canonical_name": "Mystery", "node_slug": UNSORTED_SLUG, "nature": "essential"})),
    ])

    report = await reclassify_unsorted(session, gateway=gw)

    fuel = get_node(session, "transport.car-running.fuel")
    assert len(gw.requests) == 2  # two Galp transactions, one call
    for t in (t1, t2, t3):
        session.refresh(t)
    session.refresh(m); session.refresh(mystery)
    assert t1.category_id == t2.category_id == fuel.id
    assert t3.category_id == unsorted.id
    assert m.default_category_id == fuel.id and m.confirmed is False
    assert mystery.default_category_id is None
    assert (report.merchants_resolved, report.transactions_filed, report.still_unsorted) == (1, 2, 1)


@pytest.mark.asyncio
async def test_reclassify_skips_confirmed_merchants_and_respects_direction(session):
    import json
    from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
    from app.models.transaction import TransactionType
    from app.services.taxonomy_refile import reclassify_unsorted

    ensure_taxonomy(session)
    unsorted = get_node(session, UNSORTED_SLUG)
    sm = get_node(session, "food.groceries.supermarket")
    confirmed = Merchant(canonical_name="Mine", normalized_key="mine", confirmed=True,
                         default_category_id=get_node(session, "transport.car-running.fuel").id)
    open_m = Merchant(canonical_name="Shop", normalized_key="shop")
    session.add(confirmed); session.add(open_m); session.commit()
    c1 = _txn(session, "MINE", Category.OTHER, merchant_id=confirmed.id)
    debit = _txn(session, "SHOP D", Category.OTHER, merchant_id=open_m.id)
    credit = _txn(session, "SHOP C", Category.OTHER, TransactionType.CREDIT, merchant_id=open_m.id)
    for t in (c1, debit, credit):
        file_transaction(session, t, unsorted)
    session.commit()
    gw = FakeGateway([gateway_text_result(json.dumps(
        {"canonical_name": "Shop", "node_slug": sm.slug, "nature": "essential"}))])

    report = await reclassify_unsorted(session, gateway=gw)

    for x in (c1, debit, credit, confirmed, open_m):
        session.refresh(x)
    assert len(gw.requests) == 1  # confirmed merchant never asked
    assert c1.category_id == unsorted.id
    assert confirmed.default_category_id == get_node(session, 'transport.car-running.fuel').id
    assert debit.category_id == sm.id
    assert credit.category_id == unsorted.id  # direction mismatch stays for review
    assert report.transactions_filed == 1


def test_refile_falls_back_to_unsorted_when_direction_does_not_fit(session):
    ensure_taxonomy(session)
    credit = _txn(session, "REFUND", Category.SHOPPING, TransactionType.CREDIT)
    transfer = _txn(session, "MOVE", Category.GROCERIES, TransactionType.TRANSFER)
    debit = _txn(session, "SHOP", Category.SHOPPING)
    refile_all(session, dry_run=False)
    unsorted = get_node(session, UNSORTED_SLUG)
    for t in (credit, transfer, debit):
        session.refresh(t)
    assert credit.category_id == unsorted.id
    assert transfer.category_id == unsorted.id
    assert debit.category_id == get_node(session, "personal-lifestyle.personal.general-shopping").id


@pytest.mark.parametrize("home_first", [True, False])
def test_refile_derives_a_confirmed_merchants_node_from_its_own_value_not_row_order(session, home_first):
    ensure_taxonomy(session)
    m = Merchant(canonical_name="Mod", normalized_key="mod", confirmed=True, default_category=Category.GROCERIES)
    session.add(m); session.commit()
    rows = [("MOD H", Category.HOME), ("MOD G", Category.GROCERIES)]
    if not home_first:
        rows.reverse()
    made = {name: _txn(session, name, cat, merchant_id=m.id) for name, cat in rows}
    refile_all(session, dry_run=False)
    session.refresh(m)
    sm = get_node(session, "food.groceries.supermarket")
    assert m.default_category_id == sm.id
    for t in made.values():
        session.refresh(t)
    assert made["MOD G"].category_id == sm.id
    # the HOME row follows the merchant node: same outcome whatever the row order
    assert made["MOD H"].category_id == sm.id


def test_refile_never_derives_a_merchant_node_from_a_transaction_legacy_value(session):
    ensure_taxonomy(session)
    m = Merchant(canonical_name="Vague", normalized_key="vague", confirmed=True, default_category=Category.OTHER)
    session.add(m); session.commit()
    t = _txn(session, "VAGUE", Category.GROCERIES, merchant_id=m.id)
    refile_all(session, dry_run=False)
    session.refresh(m); session.refresh(t)
    assert m.default_category_id is None
    assert t.category_id == get_node(session, "food.groceries.supermarket").id


@pytest.mark.asyncio
async def test_reclassify_confirmed_merchant_without_node_uses_legacy_no_llm(session):
    from tests.fakes.fake_gateway import FakeGateway
    from app.services.taxonomy_refile import reclassify_unsorted

    ensure_taxonomy(session)
    unsorted = get_node(session, UNSORTED_SLUG)
    m = Merchant(canonical_name="Mod", normalized_key="mod2", confirmed=True, default_category=Category.GROCERIES)
    session.add(m); session.commit()
    t = _txn(session, "MOD", Category.OTHER, merchant_id=m.id)
    file_transaction(session, t, unsorted); session.commit()
    gw = FakeGateway([])
    await reclassify_unsorted(session, gateway=gw)
    session.refresh(t); session.refresh(m)
    sm = get_node(session, "food.groceries.supermarket")
    assert gw.requests == []
    assert m.default_category_id == sm.id and t.category_id == sm.id and m.confirmed is True


@pytest.mark.asyncio
async def test_reclassify_confirmed_merchant_with_ambiguous_legacy_asks_llm_keeps_confirmed(session):
    import json
    from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
    from app.services.taxonomy_refile import reclassify_unsorted

    ensure_taxonomy(session)
    unsorted = get_node(session, UNSORTED_SLUG)
    m = Merchant(canonical_name="Amb", normalized_key="amb", confirmed=True, default_category=Category.OTHER)
    session.add(m); session.commit()
    t = _txn(session, "AMB", Category.OTHER, merchant_id=m.id)
    file_transaction(session, t, unsorted); session.commit()
    gw = FakeGateway([gateway_text_result(json.dumps(
        {"canonical_name": "Amb", "node_slug": "transport.car-running.fuel", "nature": "essential"}))])
    await reclassify_unsorted(session, gateway=gw)
    session.refresh(t); session.refresh(m)
    assert len(gw.requests) == 1
    assert t.category_id == get_node(session, "transport.car-running.fuel").id
    assert m.confirmed is True
