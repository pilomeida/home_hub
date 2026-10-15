"""Phase (b): tiers, redactor, vault, notice. Synthetic data only — every
identifier below is invented (checksum-valid but belongs to no one)."""

import pytest

from app.privacy.notices import SCAN_NOTICE, is_image_name
from app.privacy.redactor import Redactor, valid_iban, valid_luhn, valid_nib, valid_nif
from app.privacy.tiers import Tier, TierBlocked, parse_tier, policy_for
from app.privacy.vault import Vault, VaultKeyError, generate_key

IBAN = "PT50003501230001234567818"
IBAN_SPACED = "PT50 0035 0123 0001 2345 6781 8"
NIB = "003501230001234567818"
NIF = "123456789"
CARD = "4111 1111 1111 1111"
KNOWN = {"NAME": ["Ana Exemplo"], "ADDRESS": ["Rua das Flores 12, 1000-001 Lisboa"], "POLICY": ["AP-0099887"]}
SAMPLE = (f"Titular: Ana Exemplo, NIF {NIF}. IBAN {IBAN_SPACED}, NIB {NIB}, cartão {CARD}, "
          f"ana@example.com, tel +351 212 345 678. Apólice AP-0099887 em Rua das Flores 12, 1000-001 Lisboa. "
          "Total 1234,50 EUR.")


# --- checksums -------------------------------------------------------------

def test_checksums_accept_valid_and_reject_invalid():
    assert valid_iban(IBAN) and valid_iban(IBAN_SPACED) and not valid_iban("PT50003501230001234567819")
    assert valid_nib(NIB) and not valid_nib("003501230001234567819")
    assert valid_luhn(CARD) and not valid_luhn("4111 1111 1111 1112")
    assert valid_nif(NIF) and not valid_nif("123456780") and not valid_nif("023456789")


# --- redactor ---------------------------------------------------------------

def test_every_identifier_is_replaced_and_none_leaks():
    out = Redactor(KNOWN).redact_or_block(SAMPLE, Vault())
    for secret in (IBAN, IBAN_SPACED, NIB, NIF, "4111", "ana@example.com", "212 345 678",
                   "Ana", "Exemplo", "AP-0099887", "Rua das Flores", "1000-001"):
        assert secret not in out.text, secret
    assert "1234,50 EUR" in out.text            # amounts are not identifiers
    assert out.report.replaced == sum(out.report.by_kind.values()) >= 9


def test_same_value_same_placeholder_across_spellings():
    vault = Vault()
    r = Redactor(KNOWN)
    a = r.redact(f"conta {IBAN}", vault).text
    b = r.redact(f"outra vez {IBAN_SPACED}", vault).text
    assert a.split()[-1] == b.split()[-1] == "[IBAN_1]"
    assert len(vault) == 1


def test_different_values_get_different_placeholders():
    vault = Vault()
    out = Redactor().redact(f"{IBAN} e PT50001004560009876543257 e NIF {NIF}", vault)
    assert out.text == "[IBAN_1] e [IBAN_2] e NIF [NIF_1]"


def test_random_digits_and_amounts_are_not_redacted():
    text = "Fatura 2024/0012345 valor 1.234.567,89 EUR ref 000123456"
    assert Redactor().redact(text, Vault()).text == text


def test_nif_after_keyword_is_redacted_even_without_valid_checksum():
    out = Redactor().redact("Nº Contribuinte: 123 456 780", Vault())
    assert "123" not in out.text and "[NIF_1]" in out.text


def test_lookup_is_case_and_accent_insensitive_and_catches_name_parts():
    out = Redactor({"NAME": ["Ana Exemplo"]}).redact("A sr.ª ANA exémplo e Exemplo", Vault())
    assert "ANA" not in out.text and "exémplo" not in out.text.lower().replace("[", "")


def test_round_trip_restores_original_text():
    vault = Vault()
    out = Redactor(KNOWN).redact_or_block(SAMPLE, vault)
    assert vault.restore(out.text) == SAMPLE


def test_restore_does_not_clobber_longer_placeholders():
    vault = Vault()
    for i in range(10):
        vault.placeholder_for("NIF", f"10000000{i}", f"10000000{i}")
    vault.placeholder_for("NIF", "999999990", "999999990")   # -> [NIF_11]
    assert vault.restore("[NIF_1] [NIF_10] [NIF_11]") == "100000000 100000009 999999990"


def test_incomplete_redaction_blocks_the_call():
    class Leaky(Redactor):
        def redact(self, text, vault):
            from app.privacy.redactor import Redacted, RedactionReport
            return Redacted(text, RedactionReport(len(text), len(text), 0))

    with pytest.raises(TierBlocked, match="incomplete"):
        Leaky().redact_or_block(f"IBAN {IBAN}", Vault())


def test_redactor_failure_blocks_the_call():
    class Broken(Redactor):
        def find(self, text):
            raise RuntimeError("boom")

    with pytest.raises(TierBlocked, match="failed"):
        Broken().redact_or_block("x", Vault())


def test_report_has_counts_and_sizes_but_no_content():
    out = Redactor(KNOWN).redact(SAMPLE, Vault())
    blob = repr(out.report)
    assert IBAN not in blob and "Ana" not in blob
    assert out.report.chars_in == len(SAMPLE) and out.report.chars_out == len(out.text)


# --- vault ------------------------------------------------------------------

def test_sealed_vault_round_trips_and_is_not_plaintext():
    key = generate_key()
    vault = Vault()
    ph = vault.placeholder_for("IBAN", IBAN, IBAN_SPACED)
    sealed = vault.seal(key)
    assert IBAN.encode() not in sealed and IBAN_SPACED.encode() not in sealed
    reopened = Vault.open(sealed, key)
    assert reopened.restore(ph) == IBAN_SPACED
    assert reopened.placeholder_for("IBAN", IBAN) == ph          # still stable after reopening


def test_wrong_or_missing_key_cannot_open(monkeypatch):
    sealed = Vault().seal(generate_key())
    with pytest.raises(VaultKeyError):
        Vault.open(sealed, generate_key())
    monkeypatch.delenv("HUB_PRIVACY_KEY", raising=False)
    with pytest.raises(VaultKeyError):
        Vault().seal()


# --- tiers --------------------------------------------------------------------

def test_policies_follow_pedros_definition():
    assert policy_for("house_low").requires_zdr is False
    assert policy_for("house_mid").requires_zdr is True


def test_high_has_no_route_and_unknown_tiers_fail_closed():
    with pytest.raises(TierBlocked, match="no route"):
        policy_for(Tier.HIGH)
    for bad in ("house_medium", "", None, "HIGH", "top"):
        with pytest.raises(TierBlocked):
            parse_tier(bad)


def test_tier_names_do_not_collide_with_gateway_tiers():
    assert {t.value for t in Tier}.isdisjoint({"low", "high", "top", "personal", "private", "business", "public"})


# --- scan / photo notice -------------------------------------------------------

def test_image_names_get_the_notice_documents_do_not():
    assert is_image_name("IMG_001.JPG") and is_image_name("telegram-photo-x.jpg")
    assert not is_image_name("fatura.pdf")


def test_telegram_reply_carries_notice_only_for_images():
    import asyncio
    from app.channels.telegram_bot import file_to_inbox
    from app.services.inbox_service import InboxReceipt

    class Doc:  # the real receipt shape is built by receive_document; only .duplicate is read for the notice
        status = None
    receipt = InboxReceipt(document=Doc(), inbox_item=None, duplicate=False)

    async def fake_receive(session, incoming, context_text=None, external_ref=None):
        return receipt

    class FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def run(name):
        return asyncio.run(file_to_inbox(sender_name="T", filename=name, content=b"x", caption=None,
                                         message_ref="r", session_factory=FakeSession, base_url="http://h",
                                         receive=fake_receive))

    assert SCAN_NOTICE in run("photo.jpg")
    assert SCAN_NOTICE not in run("bill.pdf")
