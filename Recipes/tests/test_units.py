import pytest
from app.units import convert_ingredient, convert_ingredients


class TestMass:
    def test_ounces(self):
        assert convert_ingredient("4 oz parmesan") == "113g parmesan"

    def test_ounces_decimal(self):
        assert convert_ingredient("3.5 oz cream cheese") == "99g cream cheese"

    def test_pounds(self):
        assert convert_ingredient("1 lb ground beef") == "454g ground beef"

    def test_pounds_plural(self):
        assert convert_ingredient("2 lbs chicken") == "907g chicken"

    def test_pounds_large_shows_kg(self):
        assert convert_ingredient("5 lbs flour") == "2.3kg flour"

    def test_pound_spelled_out(self):
        assert convert_ingredient("1 pound butter") == "454g butter"

    def test_pounds_spelled_out_plural(self):
        assert convert_ingredient("2 pounds turkey") == "907g turkey"


class TestVolume:
    def test_fluid_ounce(self):
        assert convert_ingredient("2 fl oz cream") == "59ml cream"

    def test_fluid_ounce_dotted(self):
        assert convert_ingredient("2 fl. oz cream") == "59ml cream"

    def test_fluid_ounce_spelled(self):
        assert convert_ingredient("1 fluid ounce rum") == "30ml rum"

    def test_pint(self):
        assert convert_ingredient("1 pint milk") == "473ml milk"

    def test_pint_abbreviation(self):
        assert convert_ingredient("1 pt milk") == "473ml milk"

    def test_quart(self):
        assert convert_ingredient("1 quart broth") == "946ml broth"

    def test_quart_abbreviation(self):
        assert convert_ingredient("2 qt water") == "1.9L water"

    def test_gallon(self):
        assert convert_ingredient("1 gallon water") == "3.8L water"

    def test_gallon_abbreviation(self):
        assert convert_ingredient("1 gal milk") == "3.8L milk"

    def test_gallon_fraction(self):
        assert convert_ingredient("1/2 gallon milk") == "1.9L milk"


class TestFractions:
    def test_simple_fraction(self):
        assert convert_ingredient("1/2 lb butter") == "227g butter"

    def test_mixed_number(self):
        assert convert_ingredient("1 1/2 lbs pork") == "680g pork"

    def test_unicode_fraction(self):
        assert convert_ingredient("½ lb butter") == "227g butter"

    def test_unicode_quarter(self):
        assert convert_ingredient("¼ lb cheddar") == "113g cheddar"


class TestNoConversion:
    def test_cups_unchanged(self):
        assert convert_ingredient("2 cups flour") == "2 cups flour"

    def test_tablespoon_unchanged(self):
        assert convert_ingredient("1 tablespoon olive oil") == "1 tablespoon olive oil"

    def test_teaspoon_unchanged(self):
        assert convert_ingredient("1 teaspoon salt") == "1 teaspoon salt"

    def test_grams_unchanged(self):
        assert convert_ingredient("200g chocolate") == "200g chocolate"

    def test_kg_unchanged(self):
        assert convert_ingredient("1kg potatoes") == "1kg potatoes"

    def test_ml_unchanged(self):
        assert convert_ingredient("250ml milk") == "250ml milk"

    def test_section_header_unchanged(self):
        assert convert_ingredient("--- SAUCE ---") == "--- SAUCE ---"

    def test_plain_text_unchanged(self):
        assert convert_ingredient("pinch of salt") == "pinch of salt"


class TestListConversion:
    def test_converts_all(self):
        result = convert_ingredients([
            "1 lb ground beef",
            "2 cups breadcrumbs",
            "4 oz parmesan",
        ])
        assert result == ["454g ground beef", "2 cups breadcrumbs", "113g parmesan"]

    def test_empty_list(self):
        assert convert_ingredients([]) == []
