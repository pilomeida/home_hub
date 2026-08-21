from app.services.classification_engine import normalize_provider


def test_normalize_provider_strips_trailing_store_code():
    assert normalize_provider("MODELO HIPER 2640-MAFR") == "modelo hiper"


def test_normalize_provider_strips_trailing_location_word():
    assert normalize_provider("MODELO HIPER MAFRA") == "modelo hiper"


def test_normalize_provider_leaves_bare_name_unchanged():
    assert normalize_provider("MODELO HIPER") == "modelo hiper"


def test_normalize_provider_strips_repeated_trailing_location_words():
    assert normalize_provider("ALDI MAFRA MAFRA") == "aldi"


def test_normalize_provider_strips_single_trailing_location_word():
    assert normalize_provider("INTERMARCHE MAFRA") == "intermarche"


def test_normalize_provider_does_not_strip_fused_substring():
    # "ACMAFRA" ends in "mafra" as a substring of one fused word, not a
    # separate trailing location token preceded by whitespace — must not
    # be stripped, since that would corrupt an unrelated merchant name.
    assert normalize_provider("AUTOMAFRA - PNEUS ACMAFRA") == "automafra - pneus acmafra"


def test_normalize_provider_collapses_whitespace():
    assert normalize_provider("  MODELO   HIPER  ") == "modelo hiper"
