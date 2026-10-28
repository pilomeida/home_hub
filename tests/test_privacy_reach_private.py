"""Reach with private (High) types: reachable, tiered, and hideable for the first call."""

import pytest

from app.models.domain import Domain
from app.privacy.reach import compute_reach
from app.privacy.tiers import Tier
from tests import privacy_world as pw
from tests.knowledge_factories import make_document, make_record


def test_a_high_type_is_reachable_but_private(session):
    pw.set_levels(session, mid=["financials/bill"], high=["insurance/home_insurance"])
    reach = compute_reach(session)
    a = reach.access[("insurance", "home_insurance")]
    assert a.included and a.private and a.reason is None and a.tier == Tier.HIGH
    assert reach.is_private_type("insurance", "home_insurance")
    assert not reach.is_private_type("financials", "bill")


def test_the_starting_level_ignores_private_types(session):
    pw.set_levels(session, low=["house/house_appliance"], mid=["financials/bill"], high=["insurance/home_insurance"])
    assert compute_reach(session).tier == Tier.MID
    pw.set_levels(session, high=["insurance/home_insurance"])
    assert compute_reach(session).tier == Tier.MID          # nothing else included: the Mid floor


def test_health_is_private_whether_or_not_levels_are_binding(session):
    for on in (False, True):
        pw.set_levels(session, on=on, mid=["financials/bill"])
        reach = compute_reach(session)
        assert reach.type_tier("health", "lab_result") == Tier.HIGH
        assert reach.is_private_type("health", "lab_result")
        assert reach.includes_type("health", "lab_result")


def test_health_is_private_when_levels_are_not_binding_and_everything_else_is_untouched(session):
    pw.set_levels(session, on=False)
    reach = compute_reach(session)
    assert reach.binding is False
    assert reach.type_tier("house", "house_appliance") is None
    assert reach.includes_type("house", "house_appliance")


def test_without_private_hides_private_documents_records_files_and_ids(session):
    pw.set_levels(session, mid=["financials/bill"])
    clean = make_document(session, domain=Domain.FINANCIALS, category="bill")
    health = make_document(session, domain=Domain.HEALTH, category="lab_result")
    rec = make_record(session, domain=Domain.HEALTH, category="appointment")
    full = compute_reach(session)
    assert full.includes_document(health) and full.includes_record(rec)
    view = full.without_private()
    assert view.includes_document(clean) and not view.includes_document(health)
    assert not view.includes_record(rec)
    assert view.included_document_ids(session) == {clean.id}
    assert not view.includes_file_of(session, health)


def test_without_private_filters_even_when_levels_are_not_binding(session):
    pw.set_levels(session, on=False)
    clean = make_document(session, domain=Domain.HOUSE, category="house_appliance")
    health = make_document(session, domain=Domain.HEALTH, category="lab_result")
    view = compute_reach(session).without_private()
    assert view.filtering and view.included_document_ids(session) == {clean.id}
    assert not view.includes_document(health)


def test_private_tier_and_domain_helpers(session):
    pw.set_levels(session, mid=["financials/bill"])
    reach = compute_reach(session)
    clean = make_document(session, domain=Domain.FINANCIALS, category="bill")
    health = make_document(session, domain=Domain.HEALTH, category="medical_report")
    assert reach.private_tier([clean]) is None
    assert reach.private_tier([clean, health]) == Tier.HIGH
    assert reach.tier_of_source(health) == Tier.HIGH and reach.tier_of_source(clean) == Tier.MID     # a non-private type reports its saved level
    assert reach.domain_is_private("health") and reach.domain_has_private("health")
    assert not reach.domain_is_private("financials") and not reach.domain_has_private("financials")
    assert reach.has_private()


def test_a_wiki_claim_from_a_private_document_is_hidden_in_the_first_call_view(session):
    from tests.knowledge_factories import make_claim, make_page
    pw.set_levels(session, mid=["financials/bill"])
    health = make_document(session, domain=Domain.HEALTH, category="lab_result")
    page = make_page(session, "Maria: tests", domain=Domain.HEALTH)
    claim = make_claim(session, page, "cholesterol", "210", doc_ids=[health.id])
    full = compute_reach(session)
    assert full.wiki_view(session).page_visible(page.id)
    view = full.without_private().wiki_view(session)
    assert not view.page_visible(page.id) and not view.claim_visible(claim.id)


def test_health_is_never_reported_as_left_out_or_as_having_no_level(session):
    from app.services.ask.tools import settled_entries  # noqa: F401  (left_out reads the settled entries)
    pw.set_levels(session, mid=["financials/bill"])
    make_document(session, domain=Domain.HEALTH, category="lab_result")
    make_record(session, domain=Domain.HEALTH, category="appointment")
    full = compute_reach(session)
    assert full.access[("health", "lab_result")].reason is None                 # never UNSET
    for reach in (full, full.without_private()):                                # the first-call view too
        out = reach.left_out(session)
        assert all(t.reason != "unset" for t in out.types)
        assert not any(k.startswith("health/") for k in out.excluded)
        problem = out.problem()
        assert problem is None or "no level yet" not in f"{problem.why}"
