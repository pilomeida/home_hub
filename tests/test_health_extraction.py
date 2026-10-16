import json

import pytest

from app.domains.health.extraction import HealthExtractionError, extract_health_document
from app.domains.health.store import measurement_needs_check, parse_amount
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result

REPLY = {
    "person_name": "Maria Santos Almeida", "document_date": "2026-03-12", "provider": "Laboratório Norte",
    "provider_phone": "+351 22 000 0000", "provider_address": "Rua A 1, Porto", "record_kind": "test",
    "areas": ["Heart and circulation"], "conditions": ["High cholesterol"],
    "summary": "Annual blood panel; LDL above range.",
    "amount_billed": 45.5, "amount_reimbursed": None, "amount_owed": None,
    "appointment_date": None, "appointment_time": None,
    "measurements": [
        {"test": "LDL cholesterol", "value": 162, "value_text": None, "unit": "mg/dL", "ref_low": None,
         "ref_high": 100, "measured_on": "2026-03-12", "uncertain": False, "source_text": "LDL 162 mg/dL (<100)"},
        {"test": "Glucose", "value": None, "value_text": "illegible", "unit": None, "ref_low": 70, "ref_high": 99,
         "measured_on": "2026-03-12", "uncertain": True, "source_text": "Glucose ??"},
    ],
    "full_text": "LABORATÓRIO NORTE ... LDL 162 mg/dL ...",
}


def _png(tmp_path):
    p = tmp_path / "lab.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nx")
    return str(p)


@pytest.mark.asyncio
async def test_the_reply_is_parsed_into_typed_values(tmp_path):
    fake = FakeGateway([gateway_text_result(json.dumps(REPLY))])
    got = await extract_health_document(_png(tmp_path), "lab_result", existing_areas=["Eyes"],
                                        existing_conditions=["Stroke"], gateway=fake)
    assert got.person_name == "Maria Santos Almeida" and got.document_date.isoformat() == "2026-03-12"
    assert got.areas == ["Heart and circulation"] and got.conditions == ["High cholesterol"]
    ldl, glu = got.measurements
    assert (ldl.value, ldl.unit, ldl.ref_high) == (162.0, "mg/dL", 100.0)
    assert glu.value is None and glu.uncertain is True
    req = fake.requests[0]
    assert req["workload_type"] == "health_extraction" and req["attachments"]
    assert "Eyes" in req["system"] and "Stroke" in req["system"]               # reuse the existing names


@pytest.mark.asyncio
async def test_a_bad_reply_raises_a_plain_error(tmp_path):
    fake = FakeGateway([gateway_text_result("not json")])
    with pytest.raises(HealthExtractionError):
        await extract_health_document(_png(tmp_path), "lab_result", gateway=fake)


@pytest.mark.asyncio
async def test_a_file_the_model_cannot_read_is_refused_before_any_call(tmp_path):
    video = tmp_path / "x.mp4"
    video.write_bytes(b"x")
    fake = FakeGateway([])
    with pytest.raises(HealthExtractionError):
        await extract_health_document(str(video), "lab_result", gateway=fake)
    assert fake.requests == []


def test_amounts_accept_portuguese_and_english_formats():
    assert parse_amount("1.234,56") == 1234.56
    assert parse_amount("45,5 €") == 45.5
    assert parse_amount("1234.56") == 1234.56
    assert parse_amount("") is None and parse_amount(None) is None and parse_amount("abc") is None


def test_a_measurement_needs_checking_when_unsure_empty_or_a_bare_number():
    from app.domains.health.extraction import ExtractedMeasurement as M
    ok = M("LDL", 162.0, None, "mg/dL", None, 100.0, None, False, "")
    assert not measurement_needs_check(ok)
    assert measurement_needs_check(M("LDL", 162.0, None, "mg/dL", None, 100.0, None, True, ""))
    assert measurement_needs_check(M("Glucose", None, None, None, 70.0, 99.0, None, False, ""))      # no result at all
    assert measurement_needs_check(M("LDL", 162.0, None, None, None, None, None, False, ""))         # bare number
    # a result that is a limit, a word, or a unitless number with a range is data, not a doubt
    assert not measurement_needs_check(M("Microalbuminúria", None, "<3", "mg/L", None, 20.0, None, False, ""))
    assert not measurement_needs_check(M("Nitritos", None, "não detectável", None, None, None, None, False, ""))
    assert not measurement_needs_check(M("pH", 6.0, None, None, 5.0, 7.5, None, False, ""))


def test_the_prompt_asks_for_every_data_point_and_the_papers_language():
    from app.domains.health.extraction import _SYSTEM
    assert "language of the paper" in _SYSTEM and "não detectável" in _SYSTEM and '"<3"' in _SYSTEM
    assert "previous result" in _SYSTEM and "details" in _SYSTEM
