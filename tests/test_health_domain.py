"""The Health domain is registered, classifiable, and always High whatever the privacy switches say."""

import pytest

from app.domains.registry import get_spec, implemented_domains, is_implemented
from app.models.domain import Domain
from app.privacy import settings as ps
from app.privacy.scope import current_scope, privacy_scope
from app.privacy.tiers import Tier, TierBlocked
from app.services.domain_classifier import build_system_prompt

HEALTH_CATEGORIES = {"lab_result", "imaging_report", "medical_report", "prescription", "referral",
                     "vaccination", "appointment"}


def test_health_is_a_registered_domain_with_the_agreed_categories():
    assert Domain.HEALTH.value == "health" and is_implemented(Domain.HEALTH)
    spec = get_spec(Domain.HEALTH)
    assert {c.value for c in spec.categories} == HEALTH_CATEGORIES
    assert spec.home_url == "/health/records"


def test_the_classifier_no_longer_offers_bills_under_health():
    prompt = build_system_prompt(implemented_domains())
    assert 'domain "health"' in prompt and 'category "claim_statement"' not in prompt
    assert 'category "medical_bill"' not in prompt
    insurance_line = next(l for l in prompt.splitlines() if 'category "health_insurance"' in l)
    assert "reimburse" in insurance_line.lower() and "bills" in insurance_line.lower()   # points claims to Bills


def test_health_fields_mark_what_must_never_leave_the_hub():
    spec = get_spec(Domain.HEALTH)
    private = {f.key for f in spec.fields if f.private}
    admin = {f.key for f in spec.fields if not f.private}
    assert {"record_kind", "summary", "notes", "indication", "clinical_notes", "requested_by", "performed_by"} <= private
    assert {"person", "document_date", "provider", "provider_phone", "provider_address", "appointment_date",
            "appointment_time"} <= admin


def test_health_is_high_even_when_levels_are_not_binding(session):
    assert ps.enforcing(session) is False
    res = ps.resolve(session, Domain.HEALTH, "lab_result")
    assert (res.state, res.tier, res.redact) == ("ready", Tier.HIGH, False)
    assert ps.configured_tier(session, Domain.HEALTH, "lab_result") == "house_high"
    # other domains are unchanged
    assert ps.resolve(session, Domain.HOUSE, "house_appliance").state == "off"


def test_the_privacy_scope_for_health_carries_high_and_never_holds(session):
    with privacy_scope(session, Domain.HEALTH, "lab_result") as scope:
        assert scope.tier == Tier.HIGH and scope.redaction is None
        assert current_scope().tier == Tier.HIGH


def test_health_cannot_be_given_another_level(session):
    with pytest.raises(TierBlocked) as exc:
        ps.save_type(session, "health", "lab_result", "house_mid", False, "test")
    assert "always" in exc.value.reason.lower()


def test_type_rows_show_health_as_locked_high(session):
    rows = {(r.info.domain, r.info.category): r for r in ps.type_rows(session)}
    row = rows[("health", "lab_result")]
    assert row.locked and row.tier == Tier.HIGH and row.shown_tier == Tier.HIGH and row.is_saved
    assert rows[("house", "house_appliance")].locked is False


def test_confirm_suggestions_never_touches_health(session):
    ps.confirm_suggestions(session, "test")
    assert ps._setting(session, "health", "lab_result") is None


def test_a_whole_domain_lookup_for_health_is_high_too(session):
    from app.privacy.scope import resolve_strictest
    assert ps.enforcing(session) is False
    res = resolve_strictest(session, Domain.HEALTH, None)
    assert (res.state, res.tier, res.redact) == ("ready", Tier.HIGH, False)
    assert resolve_strictest(session, Domain.HOUSE, None).state == "off"


class _NoJobs:
    """A stand-in for the gateway's pairings list, so the Privacy page never reaches the process-global gateway."""

    async def pairings(self):
        return []


@pytest.fixture()
def pairings(client):
    from app.main import app
    from app.routers.privacy import get_privacy_gateway
    app.dependency_overrides[get_privacy_gateway] = lambda: _NoJobs()
    yield
    app.dependency_overrides.pop(get_privacy_gateway, None)


def test_the_privacy_page_shows_health_as_high_always(client, pairings):
    page = client.get("/privacy").text
    assert page.count("High (always)") == 7
    assert "Health papers are always private" in page


def test_changing_a_health_level_is_refused_with_a_three_part_message(client, session, pairings):
    from sqlmodel import select
    from app.models.privacy import DocumentTypeSetting
    r = client.post("/privacy/types", data={"domain": "health", "category": "lab_result", "tier": "house_low"},
                    follow_redirects=True)
    assert r.status_code == 200
    assert "Not saved." in r.text
    assert "Health papers are always High, so their level cannot be changed." in r.text
    assert "Nothing to do: they are already as private as the Hub can make them." in r.text
    assert session.exec(select(DocumentTypeSetting)).all() == []
