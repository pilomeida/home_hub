from app.models.budget import Budget
from app.models.category_node import CategoryNode
from app.models.document import Document, DocumentSource
from app.models.merchant import Merchant
from app.models.transaction import Category, Transaction, TransactionType
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy, get_node, legacy_category_for
from app.services.taxonomy_migration import remap_to_current_tree, seed_slugs
from app.services.taxonomy_slugmap import SLUG_MAP


def _old_node(session, slug, kind="out", cadence="monthly"):
    """An old-tree node, parents created on the way (the way the live DB has them)."""
    parent = None
    parts = slug.split(".")
    for level in range(1, len(parts) + 1):
        s = ".".join(parts[:level])
        node = session.query(CategoryNode).filter_by(slug=s).first()
        if node is None:
            node = CategoryNode(slug=s, parent_id=parent.id if parent else None, level=level,
                                name=parts[level - 1], kind=kind, cadence=cadence, sort_order=999)
            session.add(node); session.flush()
        parent = node
    session.commit()
    return parent


_n = [0]


def _txn(session, node, merchant=None):
    _n[0] += 1
    doc = Document(filename=f"m{_n[0]}.pdf", file_path=f"/tmp/m{_n[0]}.pdf",
                   content_hash=f"mig-{_n[0]}", source=DocumentSource.MANUAL)
    session.add(doc); session.commit()
    t = Transaction(document_id=doc.id, provider="X", category=Category.OTHER, amount=10.0,
                    transaction_type=TransactionType.DEBIT, category_id=node.id,
                    merchant_id=merchant.id if merchant else None)
    session.add(t); session.commit(); session.refresh(t)
    return t


def test_every_mapping_target_exists_in_the_seed_and_no_old_slug_is_reused_with_another_meaning():
    slugs = seed_slugs()
    for old, new in SLUG_MAP.items():
        assert new is None or new in slugs, (old, new)
        assert old != new
        assert old not in slugs, f"{old} is still a seed slug but is remapped to {new}"


def test_remap_moves_transactions_merchants_and_budgets_and_merges(session):
    ensure_taxonomy(session)  # creates the NEW tree; the old one is added by hand below
    net = _old_node(session, "housing.utilities.internet-tv")
    mobile = _old_node(session, "housing.utilities.mobile-phones")
    t1, t2 = _txn(session, net), _txn(session, mobile)
    m = Merchant(canonical_name="Vodafone", normalized_key="vodafone", default_category_id=net.id, confirmed=True)
    session.add(m); session.commit()
    session.add(Budget(node_id=net.id, year=2026, amount=30.0)); session.add(Budget(node_id=mobile.id, year=2026, amount=10.0))
    session.commit()

    report = remap_to_current_tree(session)

    telecom = get_node(session, "housing.utilities.telecom")
    session.refresh(t1); session.refresh(t2); session.refresh(m)
    assert t1.category_id == t2.category_id == telecom.id
    assert m.default_category_id == telecom.id and m.confirmed is True
    budgets = session.query(Budget).all()
    assert [(b.node_id, b.amount) for b in budgets] == [(telecom.id, 40.0)]  # merged budgets add up
    assert session.query(CategoryNode).filter_by(slug="housing.utilities.internet-tv").first() is None
    assert report.transactions_moved == 2 and report.nodes_deleted >= 2


def test_retired_node_sends_transactions_to_unsorted_and_clears_merchant_defaults(session):
    ensure_taxonomy(session)
    other = _old_node(session, "income.gifts-other.other-income", kind="in", cadence=None)
    m = Merchant(canonical_name="Someone", normalized_key="someone", default_category_id=other.id)
    session.add(m); session.commit()
    t = _txn(session, other, m)

    report = remap_to_current_tree(session)

    session.refresh(t); session.refresh(m)
    assert t.category_id == get_node(session, UNSORTED_SLUG).id and m.default_category_id is None
    assert (report.to_unsorted, report.merchants_defaults_cleared) == (1, 1)
    assert session.query(CategoryNode).filter_by(slug="income.gifts-other.other-income").first() is None
    assert session.query(CategoryNode).filter_by(slug="income.gifts-other").first() is not None  # still has Gifts received


def test_loan_insurance_and_repayments_land_on_the_new_leaves(session):
    ensure_taxonomy(session)
    life = _old_node(session, "loans-debt.loan-insurance.life-insurance-loan")
    back = _old_node(session, "loans-debt-in.repayments-received.from-family", kind="in", cadence="loan")
    a, b = _txn(session, life), _txn(session, back)
    remap_to_current_tree(session)
    session.refresh(a); session.refresh(b)
    assert a.category_id == get_node(session, "insurances.home.life-insurance-house-loan").id
    assert b.category_id == get_node(session, "loans-debt.money-lent-out.from-family").id


def test_remap_is_idempotent_and_keeps_nodes_that_still_hold_data(session):
    ensure_taxonomy(session)
    odd = _old_node(session, "zzz-old-group.cat.leaf")
    _txn(session, odd)  # not in SLUG_MAP, not in the seed, but holds data
    first = remap_to_current_tree(session)
    second = remap_to_current_tree(session)
    assert "zzz-old-group.cat.leaf" in first.nodes_kept_with_data
    assert (second.transactions_moved, second.nodes_created) == (0, 0)


def test_new_seed_shape(session):
    ensure_taxonomy(session)
    # repeated names resolve by group: Personal exists under Insurances and Family
    assert legacy_category_for(session, get_node(session, "family.personal.general-shopping")) == Category.SHOPPING
    assert legacy_category_for(session, get_node(session, "insurances.personal.health-insurance")) == Category.INSURANCE
    # a leaf keeps its own cadence when it differs from its category's
    assert get_node(session, "transport.car-running-costs.car-inspection-ipo").cadence == "yearly"
    assert get_node(session, "insurances.home.life-insurance-house-loan").cadence == "monthly"
    assert get_node(session, "insurances.home.home-insurance").cadence == "yearly"
    # a group can hold both directions; each leaf carries its own kind
    assert get_node(session, "loans-debt.money-lent-out.to-family").kind == "out"
    assert get_node(session, "loans-debt.money-lent-out.from-family").kind == "in"
    assert get_node(session, "loans-debt.money-borrowed.mortgage-drawdown").kind == "in"
    assert legacy_category_for(session, get_node(session, "loans-debt.money-lent-out.to-family")) == Category.OTHER_EXPENSE
    assert get_node(session, "income.freelance.ballet-classes").kind == "in"
