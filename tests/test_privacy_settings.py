"""Tier wiring as Hub configuration (D12): service + screen. No tier lives in code."""

import pytest
from sqlmodel import select

from app.models.document import Document, DocumentSource
from app.models.domain import Domain
from app.models.privacy import DocumentTypeSetting, TierChange
from app.models.record import Record
from app.privacy import settings as ps
from app.privacy.backfill import backfill_tiers
from app.privacy.tiers import Tier, TierBlocked


def _doc(session, name, domain, category, tier=None):
    d = Document(filename=name, file_path=name, content_hash=name, source=DocumentSource.MANUAL,
                 domain=domain, category=category, sensitivity_tier=tier)
    session.add(d); session.commit(); session.refresh(d)
    return d


def test_every_registry_type_is_listed_with_a_suggestion_and_none_is_saved_yet(session):
    rows = ps.type_rows(session)
    keys = {(r.info.domain, r.info.category) for r in rows}
    assert ("financials", "statement") in keys and ("insurance", "home_insurance") in keys
    open_rows = [r for r in rows if not r.locked]
    assert all(r.tier is None and not r.is_saved for r in open_rows)
    assert {r.shown_tier for r in open_rows} <= {Tier.LOW, Tier.MID}     # never suggests house_high
    health = [r for r in rows if r.info.domain == "health"]               # Health is locked High, not suggested
    assert len(health) == 9 and all(r.locked and r.tier == Tier.HIGH and r.is_saved for r in health)
    assert {r.info.domain for r in rows if r.locked} == {"health"}
    assert all(s != Tier.HIGH for s in ps.SUGGESTED_TIERS.values())


def test_suggestions_cover_every_registry_type():
    assert {(i.domain, i.category) for i in ps.known_types() if i.domain not in ps.FORCED_TIERS} == set(ps.SUGGESTED_TIERS)


def test_off_by_default_so_the_live_hub_is_unchanged(session):
    assert ps.enforcing(session) is False
    assert ps.resolve(session, Domain.FINANCIALS, "statement").state == "off"
    assert ps.require_ready(session, Domain.FINANCIALS, "statement").state == "off"   # does not raise


def test_when_on_an_unset_type_is_held_and_a_set_type_is_ready(session):
    ps.set_enforcing(session, True, "pedro@example.com")
    with pytest.raises(ps.TierHeld):
        ps.require_ready(session, Domain.FINANCIALS, "statement")
    with pytest.raises(ps.TierHeld):
        ps.require_ready(session, None, None)                              # unknown type: held
    ps.save_type(session, "financials", "statement", "house_mid", False, "pedro@example.com")
    res = ps.require_ready(session, Domain.FINANCIALS, "statement")
    assert (res.state, res.tier, res.redact) == ("ready", Tier.MID, False)
    with pytest.raises(ps.TierHeld):                                       # another type is still held
        ps.require_ready(session, Domain.FINANCIALS, "bill")


def test_unsetting_a_tier_holds_the_type_again(session):
    ps.set_enforcing(session, True, None)
    ps.save_type(session, "financials", "bill", "house_low", False, None)
    ps.save_type(session, "financials", "bill", "", False, None)
    with pytest.raises(ps.TierHeld):
        ps.require_ready(session, Domain.FINANCIALS, "bill")


def test_confirming_suggestions_saves_each_unset_type_once(session):
    n = ps.confirm_suggestions(session, "pedro@example.com")
    assert n == len([i for i in ps.known_types() if i.domain not in ps.FORCED_TIERS])   # Health is locked: never saved
    assert ps.confirm_suggestions(session, "pedro@example.com") == 0
    saved = {(s.domain, s.category): s.tier for s in session.exec(select(DocumentTypeSetting))}
    assert saved[("financials", "statement")] == "house_mid" and saved[("house", "floor_plan")] == "house_low"


def test_confirming_does_not_override_a_choice_already_made(session):
    ps.save_type(session, "financials", "statement", "house_low", False, None)
    ps.confirm_suggestions(session, None)
    assert session.exec(select(DocumentTypeSetting).where(DocumentTypeSetting.category == "statement")).one().tier == "house_low"


def test_saving_retiers_documents_and_records_already_filed(session):
    a = _doc(session, "a", Domain.FINANCIALS, "statement")
    b = _doc(session, "b", Domain.FINANCIALS, "bill", tier="house_low")
    r = Record(domain=Domain.FINANCIALS, category="statement"); session.add(r); session.commit()
    ps.save_type(session, "financials", "statement", "house_mid", False, None)
    session.refresh(a); session.refresh(b); session.refresh(r)
    assert (a.sensitivity_tier, r.sensitivity_tier, b.sensitivity_tier) == ("house_mid", "house_mid", "house_low")
    ps.save_type(session, "financials", "statement", "house_low", False, None)
    session.refresh(a)
    assert a.sensitivity_tier == "house_low"
    ps.save_type(session, "financials", "statement", "", False, None)
    session.refresh(a)
    assert a.sensitivity_tier is None


def test_backfill_applies_saved_settings(session):
    a = _doc(session, "a", Domain.INSURANCE, "home_insurance")
    session.add(DocumentTypeSetting(domain="insurance", category="home_insurance", tier="house_mid")); session.commit()
    assert backfill_tiers(session) == 1
    session.refresh(a)
    assert a.sensitivity_tier == "house_mid" and backfill_tiers(session) == 0


def test_bad_input_is_rejected_not_saved(session):
    with pytest.raises(TierBlocked):
        ps.save_type(session, "financials", "statement", "house_extreme", False, None)
    with pytest.raises(TierBlocked):
        ps.save_type(session, "financials", "no_such_type", "house_low", False, None)
    assert session.exec(select(DocumentTypeSetting)).all() == []


def test_every_change_is_logged_with_who_and_old_and_new(session):
    ps.save_type(session, "financials", "statement", "house_mid", False, "pedro@example.com")
    ps.save_type(session, "financials", "statement", "house_low", True, "rute@example.com")
    ps.set_enforcing(session, True, "pedro@example.com")
    log = [(c.what, c.old_value, c.new_value, c.changed_by) for c in session.exec(select(TierChange).order_by(TierChange.id))]
    assert log == [("tier", None, "house_mid", "pedro@example.com"),
                   ("tier", "house_mid", "house_low", "rute@example.com"),
                   ("redact", "False", "True", "rute@example.com"),
                   ("enforce", "0", "1", "pedro@example.com")]


# --- the screen ---------------------------------------------------------------

def test_screen_lists_types_with_suggestions_and_explains_the_state(client, pairings):
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "Bank statement" in r.text and "Home insurance" in r.text
    assert "Suggested, not set yet" in r.text and "not binding yet" in r.text
    assert "Confirm all 14 suggestions" in r.text


def test_screen_flow_confirm_then_switch_on_then_edit(client, session, pairings):
    assert client.post("/privacy/confirm-suggestions", follow_redirects=False).status_code == 303
    page = client.get("/privacy").text
    assert "Suggested, not set yet" not in page and "Confirm all" not in page
    client.post("/privacy/enforce", data={"on": "1"})
    assert ps.enforcing(session) is True
    assert "binding</h2>" in client.get("/privacy").text and "Switch levels off" in client.get("/privacy").text
    client.post("/privacy/types", data={"domain": "financials", "category": "statement", "tier": "house_low", "redact": "1"})
    row = session.exec(select(DocumentTypeSetting).where(DocumentTypeSetting.category == "statement")).one()
    session.refresh(row)
    assert (row.tier, row.redact) == ("house_low", True)
    assert "Recent changes" in client.get("/privacy").text


def test_screen_ignores_a_stale_or_forged_form_without_error(client, session, pairings):
    r = client.post("/privacy/types", data={"domain": "nope", "category": "x", "tier": "house_low"}, follow_redirects=False)
    assert r.status_code == 303
    assert session.exec(select(DocumentTypeSetting)).all() == []


def test_screen_is_linked_from_the_menu(client, pairings):
    assert 'href="/privacy"' in client.get("/").text


# --- the AI jobs section (the gateway's existing quality/privacy levels) -------

from app.llm_gateway import GatewayError  # noqa: E402
from app.routers.privacy import get_privacy_gateway  # noqa: E402


class FakePairings:
    """Shaped exactly like the gateway's GET /worker/pairings entries."""

    def __init__(self):
        self.items = {
            "vision_extraction": {"workload_type": "vision_extraction", "model": "z-ai/glm-5.3-flash",
                                  "model_display": "GLM 5.3 Flash",
                                  "privacy_tier": {"type": "private", "override": None, "effective": "private"},
                                  "min_quality_tier": {"type": "high", "override": "low", "effective": "low"}},
            "interactive_research": {"workload_type": "interactive_research", "model": "z-ai/glm-5.3-flash",
                                     "model_display": "GLM 5.3 Flash",
                                     "privacy_tier": {"type": "private", "override": None, "effective": "private"},
                                     "min_quality_tier": {"type": "high", "override": None, "effective": "high"}},
        }
        self.calls, self.error = [], None

    async def pairings(self):
        if self.error and self.error.status == 0:
            raise self.error
        return list(self.items.values())

    async def set_pairing_tiers(self, workload_type, **overrides):
        self.calls.append((workload_type, overrides))
        if self.error:
            raise self.error
        return self.items.get(workload_type, {})


@pytest.fixture()
def pairings(client):
    from app.main import app
    fake = FakePairings()
    app.dependency_overrides[get_privacy_gateway] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_privacy_gateway, None)


def test_screen_shows_the_hubs_ai_jobs_with_current_and_standard_values(client, pairings):
    page = client.get("/privacy").text
    assert "Reading bills, statements and warranties" in page and "GLM 5.3 Flash" in page
    assert "Standard (Private" in page and "Standard (Strong models only)" in page
    assert '<option value="low" selected>' in page          # the existing override is shown as selected


def test_saving_a_job_sends_both_values_and_standard_means_inherit(client, pairings):
    r = client.post("/privacy/jobs", data={"workload_type": "vision_extraction", "privacy_tier": "business",
                                           "min_quality_tier": ""}, follow_redirects=False)
    assert r.status_code == 303 and "saved=vision_extraction" in r.headers["location"]
    assert pairings.calls == [("vision_extraction", {"privacy_tier": "business", "min_quality_tier": None})]
    assert "Saved" in client.get(r.headers["location"]).text


def test_a_refused_change_is_explained_in_plain_language_and_nothing_pretends_to_save(client, pairings):
    pairings.error = GatewayError(400, "would no longer satisfy (quality_tier)")
    r = client.post("/privacy/jobs", data={"workload_type": "vision_extraction", "privacy_tier": "",
                                           "min_quality_tier": "top"}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert "no AI model allowed to do it" in page and ">Saved<" not in page


def test_unreachable_gateway_does_not_break_the_levels_section(client, pairings):
    pairings.error = GatewayError(0, "gateway unreachable")
    page = client.get("/privacy").text
    assert "reach the AI gateway" in page and "Bank statement" in page


def test_this_screen_never_changes_the_shared_types_only_calls_the_own_pairing_endpoint(client, pairings):
    client.post("/privacy/jobs", data={"workload_type": "classification", "privacy_tier": "personal", "min_quality_tier": "low"})
    assert [c[0] for c in pairings.calls] == ["classification"]
    assert set(pairings.calls[0][1]) == {"privacy_tier", "min_quality_tier"}     # nothing else can be sent


# --- recognition rules on the screen ------------------------------------------------

from app.models.privacy import DocumentFingerprint  # noqa: E402


def test_rules_can_be_added_listed_and_deleted_on_the_screen(client, session, pairings):
    client.post("/privacy/rules", data={"kind": "sender", "pattern": " Billing@Exemplo.pt ", "doc_type": "financials/bill"})
    rule = session.exec(select(DocumentFingerprint)).one()
    assert (rule.kind, rule.pattern, rule.domain, rule.category) == ("sender", "billing@exemplo.pt", "financials", "bill")
    page = client.get("/privacy").text
    assert "billing@exemplo.pt" in page and "Financials › Bill / invoice" in page
    client.post(f"/privacy/rules/{rule.id}/delete")
    assert session.exec(select(DocumentFingerprint)).all() == []


def test_bad_rules_are_ignored(client, session, pairings):
    for data in ({"kind": "bogus", "pattern": "x", "doc_type": "financials/bill"},
                 {"kind": "words", "pattern": "  ", "doc_type": "financials/bill"},
                 {"kind": "words", "pattern": "x", "doc_type": "nope/none"}):
        assert client.post("/privacy/rules", data=data, follow_redirects=False).status_code == 303
    assert session.exec(select(DocumentFingerprint)).all() == []


def test_a_web_upload_of_a_held_type_is_refused_before_anything_is_stored(client, session, pairings, inbox_documents_dir):
    from app.models.document import Document
    ps.set_enforcing(session, True, None)
    r = client.post("/financials/bills/upload", files={"file": ("a.pdf", b"%PDF-1.3 synthetic", "application/pdf")})
    assert r.status_code == 409 and "no privacy level yet" in r.text and "Not uploaded." in r.text and "Nothing was stored" in r.text
    assert session.exec(select(Document)).all() == []
    assert not inbox_documents_dir.exists() or list(inbox_documents_dir.iterdir()) == []
