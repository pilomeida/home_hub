from app.ingredients import norm_ingredient, expand_ingredient


class TestSizeAdjectives:
    def test_large_stripped(self):
        assert norm_ingredient("large eggs") == "Egg"

    def test_medium_stripped(self):
        assert norm_ingredient("medium carrot") == "Carrot"

    def test_small_stripped(self):
        assert norm_ingredient("small onion") == "Onion"

    def test_big_fat_stripped(self):
        assert norm_ingredient("big fat banana") == "Banana"


class TestConditionAdjectives:
    def test_ripe_stripped(self):
        assert norm_ingredient("ripe banana") == "Banana"

    def test_spotted_stripped(self):
        assert norm_ingredient("spotted bananas") == "Banana"

    def test_overripe_stripped(self):
        assert norm_ingredient("overripe banana") == "Banana"

    def test_fresh_stripped(self):
        assert norm_ingredient("fresh spinach") == "Spinach"

    def test_raw_stripped(self):
        assert norm_ingredient("raw chicken breast") == "Chicken breast"


class TestPrepMethods:
    def test_grated_stripped(self):
        assert norm_ingredient("grated carrot") == "Carrot"

    def test_boiled_stripped(self):
        assert norm_ingredient("boiled egg") == "Egg"

    def test_frozen_stripped(self):
        assert norm_ingredient("frozen peas") == "Pea"

    def test_cooked_drained(self):
        assert norm_ingredient("cooked and drained chickpeas") == "Chickpea"

    def test_finely_chopped(self):
        assert norm_ingredient("finely chopped onion") == "Onion"

    def test_stacked_adjectives(self):
        assert norm_ingredient("large frozen ripe banana") == "Banana"


class TestQuantities:
    def test_number_only(self):
        assert norm_ingredient("2 eggs") == "Egg"

    def test_number_with_unit(self):
        assert norm_ingredient("200g flour") == "Flour"

    def test_cups(self):
        assert norm_ingredient("2 cups oat") == "Oat"

    def test_dl_unit(self):
        assert norm_ingredient("2 dl milk") == "Milk"

    def test_fraction(self):
        assert norm_ingredient("1/2 cup Greek yogurt") == "Greek yogurt"

    def test_large_not_eaten_as_L_unit(self):
        assert norm_ingredient("1 large cucumber") == "Cucumber"

    def test_percent_stripped(self):
        assert norm_ingredient("0% sugar corn flakes") == "Sugar corn flake"


class TestPlusPrefix:
    def test_plus_stripped(self):
        assert norm_ingredient("+ 1/2 cups water") == "Water"

    def test_amp_stripped(self):
        assert norm_ingredient("& rinsed") == ""

    def test_amp_soaked(self):
        assert norm_ingredient("& soaked") == ""

    def test_plus_no_number(self):
        assert norm_ingredient("+ whole wheat wrap") == "Whole wheat wrap"  # "whole" kept (part of compound name)



class TestOrAlternative:
    def test_or_alt_stripped(self):
        assert norm_ingredient("spinach or kale") == "Spinach"

    def test_or_alt_with_comma(self):
        assert norm_ingredient("tuna, or salmon") == "Tuna"


class TestSingularize:
    def test_plural_s(self):
        assert norm_ingredient("carrots") == "Carrot"

    def test_plural_ies(self):
        assert norm_ingredient("cherries") == "Cherry"

    def test_plural_oes(self):
        assert norm_ingredient("tomatoes") == "Tomato"

    def test_already_singular(self):
        assert norm_ingredient("egg") == "Egg"

    def test_ss_not_singularized(self):
        assert norm_ingredient("hummus") == "Hummus"

    def test_us_not_singularized(self):
        assert norm_ingredient("asparagus") == "Asparagus"


class TestBrewed:
    def test_brewed_coffee(self):
        assert norm_ingredient("brewed coffee") == "Coffee"

    def test_soaked_oats(self):
        assert norm_ingredient("soaked oats") == "Oat"


class TestBoxOf:
    def test_box_of_pasta(self):
        assert norm_ingredient("box of pasta") == "Pasta"

    def test_box_of_silken_tofu(self):
        assert norm_ingredient("box of silken tofu") == "Silken tofu"


class TestOfChoice:
    def test_berries_of_choice(self):
        assert norm_ingredient("berries of choice") == "Berry"

    def test_breadcrumbs_of_choice(self):
        assert norm_ingredient("breadcrumbs of choice") == "Breadcrumb"


class TestTrailingTo:
    def test_to_taste(self):
        assert norm_ingredient("salt to taste") == "Salt"

    def test_to_reach(self):
        assert norm_ingredient("water to reach desired consistency") == "Water"

    def test_additional_qualifier_and_clause_stripped(self):
        assert norm_ingredient("Additional water to reach your desired consistency") == "Water"


class TestSectionHeaders:
    def test_for_the_sauce_dropped(self):
        assert norm_ingredient("For the sauce:") == ""

    def test_to_serve_dropped(self):
        assert norm_ingredient("To serve") == ""


class TestExpandIngredient:
    def test_compound_split(self):
        result = expand_ingredient("salt and pepper")
        assert result == ["Salt", "Pepper"]

    def test_section_header_returns_empty(self):
        result = expand_ingredient("For the sauce:")
        assert result == []

    def test_single_ingredient(self):
        result = expand_ingredient("large frozen ripe bananas")
        assert result == ["Banana"]
