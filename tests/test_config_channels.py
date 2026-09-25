import pytest

from app.config import parse_sender_allowlist, parse_telegram_users, settings


def test_parse_sender_allowlist_normalises():
    assert parse_sender_allowlist(" Pedro@Example.com, rute@example.com ,,") == frozenset(
        {"pedro@example.com", "rute@example.com"}
    )
    assert parse_sender_allowlist("") == frozenset()


def test_parse_telegram_users():
    assert parse_telegram_users("111:Pedro, 222:Rute") == {111: "Pedro", 222: "Rute"}
    assert parse_telegram_users("") == {}


@pytest.mark.parametrize("bad", ["abc:Pedro", "111", "111:"])
def test_parse_telegram_users_rejects_bad_entries(bad):
    with pytest.raises(ValueError, match="HUB_TELEGRAM_ALLOWED_USERS"):
        parse_telegram_users(bad)


def test_channel_settings_have_safe_types_and_defaults():
    assert isinstance(settings.imap_configured, bool)
    assert isinstance(settings.HUB_IMAP_PORT, int)
    assert settings.PUBLIC_BASE_URL.startswith("https://")
