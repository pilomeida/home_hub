from datetime import date

import pytest

from app.models.merchant import Merchant
from app.models.provider_rule import ProviderRule
from app.models.transaction import TransactionType
from app.services.provider_rules import (
    apply_to_providers, find_rule_node, list_provider_groups, provider_key,
)
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy, get_node
from tests.test_merchant_reconcile import _merchant, _t

FAMILY = "loans-debt.money-lent-out.to-family"
SM, REST = "food.groceries.supermarket", "food.eat-out.restaurants"
MBW = "TRF MBWAY P/MATIAS"


def test_provider_key_ignores_numbers_case_and_accents():
    assert provider_key("TRF MBWAY P/XXXXX1225") == provider_key("trf mbway p/xxxxx9981") == "trf mbway p/xxxxx#"
    assert provider_key("Pag Serviços EUPAGO*LIGATTE 123") == "pag servicos eupago*ligatte #"
    assert provider_key("COMPRA  3315 SODIMAFRA MAFRA") == "compra # sodimafra mafra"
    assert provider_key(None) == "" and provider_key("  ") == ""


def _channel(session):
    ensure_taxonomy(session)
    m = _merchant(session, "MBWay Transfer", REST)
    return m, [_t(session, m, UNSORTED_SLUG, TransactionType.TRANSFER, provider=p)
               for p in ("TRF MBWAY P/MATIAS", "TRF MBWAY P/MATIAS", "TRF MBWAY P/JOANA")]


def test_groups_split_a_channel_merchant_by_provider_text(session):
    _channel(session)
    page = list_provider_groups(session)
    assert [(g.key, g.entries) for g in page.rows] == [("trf mbway p/matias", 2), ("trf mbway p/joana", 1)]
    assert page.rows[0].merchants == ["MBWay Transfer"] and page.rows[0].unsorted == 2
    assert [g.key for g in list_provider_groups(session, q="joana").rows] == ["trf mbway p/joana"]
    assert list_provider_groups(session, min_entries=2).total_count == 1
    assert list_provider_groups(session, date_from=date(2100, 1, 1)).total_count == 0


def test_apply_files_transfers_by_hand_and_saves_a_rule(session):
    m, rows = _channel(session)
    node = get_node(session, FAMILY)
    report = apply_to_providers(session, ["trf mbway p/matias"], node, remember=True)
    for t in rows:
        session.refresh(t)
    assert [t.category_id for t in rows] == [node.id, node.id, get_node(session, UNSORTED_SLUG).id]
    assert (report.groups, report.filed_from_unsorted, report.rules_saved) == (1, 2, 1)
    assert find_rule_node(session, "trf mbway p/Matias").id == node.id  # case and spacing do not matter
    assert find_rule_node(session, "TRF MBWAY P/JOANA") is None


def test_a_rule_made_from_a_text_with_a_reference_number_matches_any_other_number(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Pag Serviços")
    _t(session, m, UNSORTED_SLUG, provider="PAG SERVICOS EUPAGO*LIGATTE 9981")
    node = get_node(session, "housing.utilities.telecom")
    apply_to_providers(session, [provider_key("PAG SERVICOS EUPAGO*LIGATTE 9981")], node)
    assert find_rule_node(session, "PAG SERVICOS EUPAGO*LIGATTE 1234").id == node.id
    assert find_rule_node(session, "PAG SERVICOS EUPAGO*OUTRA 1234") is None


def test_dry_run_and_no_remember_write_nothing_extra(session):
    m, rows = _channel(session)
    node = get_node(session, FAMILY)
    apply_to_providers(session, ["trf mbway p/matias"], node, dry_run=True)
    session.refresh(rows[0])
    assert rows[0].category_id == get_node(session, UNSORTED_SLUG).id and session.query(ProviderRule).count() == 0
    apply_to_providers(session, ["trf mbway p/matias"], node, remember=False)
    assert session.query(ProviderRule).count() == 0


def test_loan_linked_rows_never_move_and_a_debit_cannot_take_an_inflow_node(session):
    ensure_taxonomy(session)
    m = _merchant(session, "Santander")
    loan = _t(session, m, "loans-debt.loan-repayments.mortgage", provider="COB.REC.31.000317997064096/3")
    loan.debt_id = 1; session.add(loan)
    debit = _t(session, m, UNSORTED_SLUG, provider="COB.REC.31.000317997064096/4")
    session.commit()
    node = get_node(session, "income.psi.sessions")
    report = apply_to_providers(session, ["cob.rec.#.#/#"], node)
    session.refresh(loan); session.refresh(debit)
    assert loan.category_id == get_node(session, "loans-debt.loan-repayments.mortgage").id
    assert debit.category_id == get_node(session, UNSORTED_SLUG).id and report.left_alone == 1


def test_only_a_real_leaf_is_accepted(session):
    _channel(session)
    with pytest.raises(ValueError):
        apply_to_providers(session, ["x"], get_node(session, "food"))


def test_per_entry_marks_the_merchants_as_channels(session):
    m, _ = _channel(session)
    apply_to_providers(session, ["trf mbway p/matias"], get_node(session, FAMILY), per_entry_merchants=True)
    session.refresh(m)
    assert m.by_provider is True


@pytest.mark.asyncio
async def test_new_entry_follows_the_rule_before_the_merchant_default(session):
    from app.services.classification_engine import classify_transaction, normalize_provider
    from tests.fakes.fake_gateway import FakeGateway

    ensure_taxonomy(session)
    provider = "TRF MBWAY P/MATIAS"
    m = Merchant(canonical_name="MBWay Transfer", normalized_key=normalize_provider(provider),
                 default_category_id=get_node(session, REST).id)
    session.add(m); session.commit()
    seed = _t(session, m, UNSORTED_SLUG, TransactionType.TRANSFER, provider=provider)
    apply_to_providers(session, [provider_key(provider)], get_node(session, FAMILY), remember=True)

    new = _t(session, m, UNSORTED_SLUG, TransactionType.TRANSFER, provider=provider)
    new.category_id = None; session.add(new); session.commit()
    await classify_transaction(session, new, gateway=FakeGateway([]))
    assert new.category_id == get_node(session, FAMILY).id  # the rule, not the merchant's Restaurants


@pytest.mark.asyncio
async def test_a_channel_merchant_default_is_not_applied(session):
    from app.services.classification_engine import classify_transaction
    from tests.fakes.fake_gateway import FakeGateway
    from app.services.classification_engine import normalize_provider

    ensure_taxonomy(session)
    provider = "TRANSFER FOR EVERYTHING"
    m = Merchant(canonical_name="Channel", normalized_key=normalize_provider(provider), by_provider=True,
                 default_category_id=get_node(session, REST).id)
    session.add(m); session.commit()
    t = _t(session, m, UNSORTED_SLUG, provider=provider)
    t.category_id = None; session.add(t); session.commit()
    await classify_transaction(session, t, gateway=FakeGateway([]))
    assert t.category_id is None  # waits in Needs Review

    m.by_provider = False; session.add(m); session.commit()
    await classify_transaction(session, t, gateway=FakeGateway([]))
    assert t.category_id == get_node(session, REST).id


# --- bulk page, provider mode -------------------------------------------------

def test_bulk_page_provider_mode_lists_applies_and_remembers(client, session):
    m, rows = _channel(session)
    page = client.get("/financials/transactions/bulk", params={"by": "provider", "scope": "all"})
    assert page.status_code == 200 and "TRF MBWAY P/MATIAS" in page.text and "TRF MBWAY P/JOANA" in page.text
    data = {"by": "provider", "group_keys": ["trf mbway p/matias"], "category_node": FAMILY,
            "refile_existing": "1", "remember": "1", "step": "preview"}
    preview = client.post("/financials/transactions/bulk", data=data)
    assert "Review before applying" in preview.text and "1 rule saved" in preview.text
    assert session.query(ProviderRule).count() == 0
    done = client.post("/financials/transactions/bulk", data={**data, "step": "apply"}, follow_redirects=False)
    assert done.status_code == 303 and "rules=1" in done.headers["location"]
    session.expire_all()
    assert session.query(ProviderRule).count() == 1


def test_bulk_page_filters_by_search_category_entries_and_dates(client, session):
    _channel(session)
    ok = lambda **p: client.get("/financials/transactions/bulk", params={"by": "provider", **p}).status_code  # noqa: E731
    assert ok(q="matias", cat="unsorted", min="2", date_from="2020-01-01", date_to="2100-01-01") == 200
    assert ok(date_from="not-a-date", min="x", page="") == 200  # blanks and junk never 422
    merchant_page = client.get("/financials/transactions/bulk", params={"scope": "all", "q": "mbway", "min": "3"})
    assert "MBWay Transfer" in merchant_page.text
    assert "MBWay Transfer" not in client.get("/financials/transactions/bulk", params={"scope": "all", "min": "9"}).text
    assert "MBWay Transfer" in client.get("/financials/transactions/bulk", params={"scope": "all", "q": "MATIAS"}).text  # provider text search
