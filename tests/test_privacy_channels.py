from app.domains.registry import get_spec
from app.models.domain import Domain
from app.privacy.channels import channel_fields


def test_private_fields_never_pass_the_channel_guard():
    spec = get_spec(Domain.HEALTH)
    fields = {"person": "Maria", "provider": "Clínica Sul", "document_date": "2026-03-12",
              "summary": "SECRET-SUMMARY", "record_kind": "test", "notes": "SECRET-NOTES", "amount_billed": "45,5"}
    safe = channel_fields(spec, "lab_result", fields)
    assert safe == {"person": "Maria", "provider": "Clínica Sul", "document_date": "2026-03-12", "amount_billed": "45,5"}


def test_a_domain_with_no_private_fields_passes_everything():
    spec = get_spec(Domain.INSURANCE)
    fields = {"insurer": "Zurich", "policy_number": "AP-1"}
    assert channel_fields(spec, "home_insurance", fields) == fields


def test_an_unknown_key_is_dropped_like_a_private_one():
    spec = get_spec(Domain.HEALTH)
    fields = {"person": "Maria", "provider": "Clínica Sul", "summary": "SECRET-SUMMARY", "not_a_field": "SECRET-UNKNOWN",
              "appointment_date": "2026-11-03"}
    # appointment_date is declared for appointments only: not for a lab result, so it is not an allowed key there
    assert channel_fields(spec, "lab_result", fields) == {"person": "Maria", "provider": "Clínica Sul"}
    assert channel_fields(spec, "appointment", fields) == {"person": "Maria", "provider": "Clínica Sul",
                                                           "appointment_date": "2026-11-03"}
    assert channel_fields(spec, None, {"not_a_field": "x"}) == {}
