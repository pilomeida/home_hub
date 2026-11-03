import pytest
from app.models.document import Document, DocumentSource
from app.models.merchant import Merchant
from app.models.transaction import Transaction, TransactionType
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy, file_transaction, flow_of, get_node
from app.services.taxonomy_refile import refile_all


def _txn(session, provider, ttype=TransactionType.DEBIT, merchant_id=None):
    doc = Document(filename=f"{provider}.pdf", file_path=f"/tmp/{provider}.pdf",
                   content_hash=f"h-{provider}", source=DocumentSource.MANUAL)
    session.add(doc); session.commit()
    t = Transaction(document_id=doc.id, description=provider, transaction_type=ttype, amount=10.0, merchant_id=merchant_id)
    session.add(t); session.commit(); session.refresh(t)
    return t


def test_file_transaction_files_under_the_node_only(session):
    """The tree is the only record of what a line was spent on (work-list B3); the old column keeps its filler."""
    ensure_taxonomy(session)
    t = _txn(session, "EDP")
    file_transaction(session, t, get_node(session, "housing.utilities.electricity"))
    assert t.category_id == get_node(session, "housing.utilities.electricity").id
    assert t.unused_old_category == "OTHER"


def test_flow_of_uses_kind_and_falls_back_to_type_for_unsorted(session):
    ensure_taxonomy(session)
    out_t = _txn(session, "A")
    assert flow_of(out_t, get_node(session, "food.groceries.supermarket")) == "out"
    assert flow_of(out_t, get_node(session, "income.psi.sessions")) == "in"
    assert flow_of(out_t, get_node(session, "internal-transfers.between-my-accounts.santander-card")) == "neutral"
    credit = _txn(session, "B", TransactionType.CREDIT)
    assert flow_of(credit, get_node(session, UNSORTED_SLUG)) == "in"
    assert flow_of(out_t, get_node(session, UNSORTED_SLUG)) == "out"


def test_refile_files_from_a_confirmed_merchant_else_sends_to_review(session):
    ensure_taxonomy(session)
    fuel = get_node(session, "transport.car-running-costs.fuel")
    m = Merchant(canonical_name="Galp", normalized_key="galp", confirmed=True, default_category_id=fuel.id)
    session.add(m); session.commit()
    from_merchant = _txn(session, "GALP", merchant_id=m.id)
    unknown = _txn(session, "MYSTERY")

    report = refile_all(session, dry_run=False)

    assert from_merchant.category_id == fuel.id
    assert unknown.category_id == get_node(session, UNSORTED_SLUG).id
    assert (report.filed_from_merchant, report.sent_to_review) == (1, 1)


def test_refile_dry_run_changes_nothing_and_rerun_skips_filed(session):
    ensure_taxonomy(session)
    sm = get_node(session, "food.groceries.supermarket")
    m = Merchant(canonical_name="Continente", normalized_key="continente", confirmed=True, default_category_id=sm.id)
    session.add(m); session.commit()
    t = _txn(session, "CONTINENTE", merchant_id=m.id)
    report = refile_all(session, dry_run=True)
    session.refresh(t)
    assert t.category_id is None and report.filed_from_merchant == 1
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
    t1, t2 = _txn(session, "GALP 1", merchant_id=m.id), _txn(session, "GALP 2", merchant_id=m.id)
    t3 = _txn(session, "MYSTERY", merchant_id=mystery.id)
    for t in (t1, t2, t3):
        file_transaction(session, t, unsorted)
    session.commit()
    gw = FakeGateway([
        gateway_text_result(json.dumps({"canonical_name": "Galp", "node_slug": "transport.car-running-costs.fuel", "nature": "essential"})),
        gateway_text_result(json.dumps({"canonical_name": "Mystery", "node_slug": UNSORTED_SLUG, "nature": "essential"})),
    ])

    report = await reclassify_unsorted(session, gateway=gw)

    fuel = get_node(session, "transport.car-running-costs.fuel")
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
                         default_category_id=get_node(session, "transport.car-running-costs.fuel").id)
    open_m = Merchant(canonical_name="Shop", normalized_key="shop")
    session.add(confirmed); session.add(open_m); session.commit()
    c1 = _txn(session, "MINE", merchant_id=confirmed.id)
    debit = _txn(session, "SHOP D", merchant_id=open_m.id)
    credit = _txn(session, "SHOP C", TransactionType.CREDIT, merchant_id=open_m.id)
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
    assert confirmed.default_category_id == get_node(session, 'transport.car-running-costs.fuel').id
    assert debit.category_id == sm.id
    assert credit.category_id == sm.id  # a credit from a shop is a refund under the same node
    assert report.transactions_filed == 2


def test_refile_falls_back_to_unsorted_when_direction_does_not_fit(session):
    ensure_taxonomy(session)
    shopping = get_node(session, "family.personal.general-shopping")
    shop = Merchant(canonical_name="Shop", normalized_key="shop", confirmed=True, default_category_id=shopping.id)
    session.add(shop); session.commit()
    credit = _txn(session, "REFUND", TransactionType.CREDIT)
    transfer = _txn(session, "MOVE", TransactionType.TRANSFER, merchant_id=shop.id)
    debit = _txn(session, "SHOP", merchant_id=shop.id)
    refile_all(session, dry_run=False)
    unsorted = get_node(session, UNSORTED_SLUG)
    for t in (credit, transfer, debit):
        session.refresh(t)
    assert credit.category_id == unsorted.id  # no merchant, so no purchase to refund
    assert transfer.category_id == unsorted.id  # a transfer only fits neutral nodes
    assert debit.category_id == shopping.id


def test_refile_leaves_a_confirmed_merchant_without_a_node_for_review(session):
    ensure_taxonomy(session)
    m = Merchant(canonical_name="Vague", normalized_key="vague", confirmed=True)
    session.add(m); session.commit()
    t = _txn(session, "VAGUE", merchant_id=m.id)
    refile_all(session, dry_run=False)
    session.refresh(m); session.refresh(t)
    assert m.default_category_id is None
    assert t.category_id == get_node(session, UNSORTED_SLUG).id


@pytest.mark.asyncio
async def test_reclassify_confirmed_merchant_without_a_node_asks_llm_keeps_confirmed(session):
    import json
    from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
    from app.services.taxonomy_refile import reclassify_unsorted

    ensure_taxonomy(session)
    unsorted = get_node(session, UNSORTED_SLUG)
    m = Merchant(canonical_name="Amb", normalized_key="amb", confirmed=True)
    session.add(m); session.commit()
    t = _txn(session, "AMB", merchant_id=m.id)
    file_transaction(session, t, unsorted); session.commit()
    gw = FakeGateway([gateway_text_result(json.dumps(
        {"canonical_name": "Amb", "node_slug": "transport.car-running-costs.fuel", "nature": "essential"}))])
    await reclassify_unsorted(session, gateway=gw)
    session.refresh(t); session.refresh(m)
    assert len(gw.requests) == 1
    assert t.category_id == get_node(session, "transport.car-running-costs.fuel").id
    assert m.confirmed is True


# --- concurrent reclassify ---------------------------------------------------

import asyncio
import json
import re

from app.llm_gateway import GatewayError
from tests.fakes.fake_gateway import gateway_text_result

_FUEL = "transport.car-running-costs.fuel"
_SM = "food.groceries.supermarket"


class SlowGateway:
    """Same run() signature as the gateway; sleeps, tracks calls in flight and
    answers per provider from `replies` (slug str, or an Exception to raise)."""

    def __init__(self, replies=None, default=_FUEL, delay=0.02, delays=None, hang=()):
        self.replies, self.default, self.delay = replies or {}, default, delay
        self.delays, self.hang = delays or {}, set(hang)
        self.in_flight = self.max_in_flight = 0
        self.providers: list[str] = []

    async def run(self, workload_type, system, user=None, **kw):
        provider = re.search(r"'(.+)'", user).group(1)
        self.providers.append(provider)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if provider in self.hang:
                await asyncio.sleep(3600)
            await asyncio.sleep(self.delays.get(provider, self.delay))
            reply = self.replies.get(provider, self.default)
            if isinstance(reply, Exception):
                raise reply
            return GatewayResult({"text": json.dumps(
                {"canonical_name": provider.title(), "node_slug": reply, "nature": "essential"}),
                "stop_reason": "end_turn", "usage": {}})
        finally:
            self.in_flight -= 1


from app.llm_gateway import GatewayResult  # noqa: E402


def _merchants_with_unsorted(session, counts):
    """counts: {provider: n_transactions}; every row filed under Unsorted."""
    ensure_taxonomy(session)
    unsorted = get_node(session, UNSORTED_SLUG)
    made = {}
    for provider, n in counts.items():
        m = Merchant(canonical_name=provider.title(), normalized_key=provider.lower())
        session.add(m); session.commit()
        rows = []
        for i in range(n):
            t = _txn(session, f"{provider}", merchant_id=m.id)
            t.description = provider
            file_transaction(session, t, unsorted)
            rows.append(t)
        session.commit()
        made[provider] = (m, rows)
    return made


@pytest.mark.asyncio
async def test_reclassify_runs_calls_concurrently_but_never_above_the_limit(session):
    from app.services.taxonomy_refile import reclassify_unsorted

    made = _merchants_with_unsorted(session, {f"SHOP {i:02d}": 1 for i in range(10)})
    gw = SlowGateway()
    report = await reclassify_unsorted(session, gateway=gw, concurrency=3)
    assert 1 < gw.max_in_flight <= 3
    assert sorted(gw.providers) == sorted(made)  # exactly one call per merchant
    assert report.merchants_resolved == 10 and report.transactions_filed == 10


@pytest.mark.asyncio
async def test_reclassify_applies_each_result_to_its_own_merchant_whatever_the_completion_order(session):
    from app.services.taxonomy_refile import reclassify_unsorted

    made = _merchants_with_unsorted(session, {"AAA": 1, "BBB": 1, "CCC": 1})
    # AAA is slowest, so completions arrive CCC, BBB, AAA
    gw = SlowGateway(replies={"AAA": _SM, "BBB": _FUEL, "CCC": "food.eat-out.restaurants"},
                     delays={"AAA": 0.15, "BBB": 0.08, "CCC": 0.01})
    await reclassify_unsorted(session, gateway=gw, concurrency=6)
    for provider, slug in (("AAA", _SM), ("BBB", _FUEL), ("CCC", "food.eat-out.restaurants")):
        m, rows = made[provider]
        session.refresh(m); session.refresh(rows[0])
        assert m.default_category_id == get_node(session, slug).id
        assert rows[0].category_id == get_node(session, slug).id
        assert m.confirmed is False


@pytest.mark.asyncio
async def test_reclassify_failures_are_counted_and_do_not_stop_the_others(session):
    from app.services.taxonomy_refile import reclassify_unsorted

    made = _merchants_with_unsorted(session, {"OK1": 1, "BAD": 1, "OK2": 1, "SLOW": 1})
    gw = SlowGateway(replies={"BAD": GatewayError(503, "overloaded")}, hang=("SLOW",))
    import app.services.taxonomy_refile as mod
    mod.PER_CALL_TIMEOUT = 0.2
    try:
        report = await reclassify_unsorted(session, gateway=gw, concurrency=4)
    finally:
        mod.PER_CALL_TIMEOUT = 120
    assert report.failed == 2  # one gateway error, one timeout
    assert report.merchants_resolved == 2
    for p in ("BAD", "SLOW"):
        session.refresh(made[p][1][0])
        assert made[p][1][0].category_id == get_node(session, UNSORTED_SLUG).id


@pytest.mark.asyncio
async def test_reclassify_aborts_after_20_consecutive_failures(session):
    from app.services.taxonomy_refile import reclassify_unsorted

    names = {f"M{i:02d}": 1 for i in range(30)}
    _merchants_with_unsorted(session, names)
    gw = SlowGateway(default=GatewayError(503, "down"))
    gw.default = None
    gw.replies = {n: GatewayError(503, "down") for n in names}
    lines = []
    report = await reclassify_unsorted(session, gateway=gw, concurrency=1, progress=lines.append)
    assert report.aborted is True and "20 consecutive failures" in report.abort_message
    assert report.failed == 20
    assert len(gw.providers) == 20  # nothing queued after the abort was ever called
    assert any("20 consecutive failures" in l for l in lines)


@pytest.mark.asyncio
async def test_reclassify_asks_the_merchants_with_most_unsorted_transactions_first(session):
    from app.services.taxonomy_refile import reclassify_unsorted

    _merchants_with_unsorted(session, {"BETA": 1, "ALPHA": 1, "BIG": 3, "MID": 2})
    gw = SlowGateway(delay=0.0)
    await reclassify_unsorted(session, gateway=gw, concurrency=1)
    assert gw.providers == ["BIG", "MID", "ALPHA", "BETA"]


@pytest.mark.asyncio
async def test_reclassify_reports_progress_every_25_and_at_the_end(session):
    from app.services.taxonomy_refile import reclassify_unsorted

    _merchants_with_unsorted(session, {f"P{i:02d}": 1 for i in range(30)})
    lines = []
    await reclassify_unsorted(session, gateway=SlowGateway(delay=0.0), concurrency=5, progress=lines.append)
    assert lines == [
        "reclassify: 25/30 merchants, resolved 25, still unsorted 0, failed 0, transactions filed 25",
        "reclassify: 30/30 merchants, resolved 30, still unsorted 0, failed 0, transactions filed 30",
    ]


def test_parse_concurrency_defaults_and_clamps():
    from app.services.taxonomy_refile import parse_concurrency

    assert parse_concurrency(["--reclassify"]) == 6
    assert parse_concurrency(["--reclassify", "--concurrency", "9"]) == 9
    assert parse_concurrency(["--concurrency=3"]) == 3
    assert parse_concurrency(["--concurrency", "0"]) == 1
    assert parse_concurrency(["--concurrency", "99"]) == 12
    with pytest.raises(ValueError):
        parse_concurrency(["--concurrency", "many"])
    with pytest.raises(ValueError):
        parse_concurrency(["--concurrency"])


# --- best-guess pass ---------------------------------------------------------

@pytest.mark.asyncio
async def test_best_guess_forbids_unsure_and_files_a_node_for_a_merchant_the_normal_pass_gives_up_on(session):
    from tests.fakes.fake_gateway import FakeGateway
    from app.services.taxonomy_refile import reclassify_unsorted

    made = _merchants_with_unsorted(session, {"MYSTERY": 2})
    m, rows = made["MYSTERY"]
    gw = FakeGateway([gateway_text_result(json.dumps(
        {"canonical_name": "Mystery", "node_slug": _FUEL, "nature": "essential"}))])

    report = await reclassify_unsorted(session, gateway=gw, best_guess=True)

    req = gw.requests[0]
    assert UNSORTED_SLUG not in req["response_schema"]["properties"]["node_slug"]["enum"]
    assert UNSORTED_SLUG not in req["system"]
    assert "best" in req["system"].lower()
    for t in rows:
        session.refresh(t)
    assert all(t.category_id == get_node(session, _FUEL).id for t in rows)
    session.refresh(m)
    assert m.default_category_id == get_node(session, _FUEL).id and m.confirmed is False
    assert (report.merchants_resolved, report.transactions_filed) == (1, 2)


@pytest.mark.asyncio
async def test_normal_pass_keeps_the_unsure_option(session):
    from tests.fakes.fake_gateway import FakeGateway
    from app.services.taxonomy_refile import reclassify_unsorted

    _merchants_with_unsorted(session, {"MYSTERY": 1})
    gw = FakeGateway([gateway_text_result(json.dumps(
        {"canonical_name": "Mystery", "node_slug": UNSORTED_SLUG, "nature": "essential"}))])
    await reclassify_unsorted(session, gateway=gw)
    assert UNSORTED_SLUG in gw.requests[0]["response_schema"]["properties"]["node_slug"]["enum"]


@pytest.mark.asyncio
async def test_best_guess_respects_direction_and_never_touches_confirmed_merchants(session):
    from tests.fakes.fake_gateway import FakeGateway
    from app.services.taxonomy_refile import reclassify_unsorted

    made = _merchants_with_unsorted(session, {"PAYER": 1, "PEDRO'S": 1})
    payer, (credit,) = made["PAYER"]
    credit.transaction_type = TransactionType.CREDIT
    mine, (kept,) = made["PEDRO'S"]
    mine.confirmed = True; mine.default_category_id = get_node(session, _SM).id
    session.add_all([credit, mine]); session.commit()
    gw = FakeGateway([gateway_text_result(json.dumps(
        {"canonical_name": "Payer", "node_slug": _FUEL, "nature": "essential"}))])  # a spending slug for a payer who never sold

    report = await reclassify_unsorted(session, gateway=gw, best_guess=True)

    assert len(gw.requests) == 1  # the confirmed merchant is not asked
    session.refresh(credit); session.refresh(kept)
    assert credit.category_id == get_node(session, UNSORTED_SLUG).id  # direction mismatch stays unsorted
    assert kept.category_id == get_node(session, UNSORTED_SLUG).id
    assert report.still_unsorted == 1


# --- only-undecided pass -----------------------------------------------------

@pytest.mark.asyncio
async def test_only_undecided_asks_just_the_merchants_without_a_default_node(session):
    from tests.fakes.fake_gateway import FakeGateway
    from app.services.taxonomy_refile import reclassify_unsorted

    made = _merchants_with_unsorted(session, {"DECIDED": 1, "UNDECIDED": 1})
    decided, (d_row,) = made["DECIDED"]
    undecided, (u_row,) = made["UNDECIDED"]
    decided.default_category_id = get_node(session, _SM).id
    session.add(decided); session.commit()
    gw = FakeGateway([gateway_text_result(json.dumps(
        {"canonical_name": "Undecided", "node_slug": _FUEL, "nature": "essential"}))])

    report = await reclassify_unsorted(session, gateway=gw, only_undecided=True)

    assert len(gw.requests) == 1 and "UNDECIDED" in gw.requests[0]["user"]
    session.refresh(d_row); session.refresh(u_row)
    assert d_row.category_id == get_node(session, UNSORTED_SLUG).id  # untouched, still for the merchant default
    assert u_row.category_id == get_node(session, _FUEL).id
    assert report.merchants_asked == 1
