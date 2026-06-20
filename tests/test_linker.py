import json

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import CrossLink, Recipe
from app.linker import detect_and_link, jaccard_similarity


class TestJaccardSimilarity:
    def test_identical_sets(self):
        assert jaccard_similarity({"a", "b", "c"}, {"a", "b", "c"}) == 1.0

    def test_disjoint_sets(self):
        assert jaccard_similarity({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial_overlap(self):
        result = jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"})
        assert result == 2 / 4  # intersection=2, union=4

    def test_empty_sets(self):
        assert jaccard_similarity(set(), set()) == 0.0


class TestDetectAndLink:
    @pytest.fixture
    def session(self):
        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        with Session(engine) as s:
            yield s

    def _make_recipe(self, session, dish_name, ingredients, type_="sweet", source_url_suffix="a"):
        r = Recipe(
            title=f"{dish_name} (test)",
            dish_name=dish_name,
            type=type_,
            ingredients=json.dumps(ingredients),
            source_url=f"https://ig.com/{source_url_suffix}",
        )
        session.add(r)
        session.commit()
        session.refresh(r)
        return r

    def test_links_similar_ingredients(self, session):
        r1 = self._make_recipe(session, "Bolo de Chocolate",
                               ["egg", "flour", "chocolate", "butter"], source_url_suffix="1")
        r2 = self._make_recipe(session, "Bolo de Chocolate Fitness",
                               ["egg", "flour", "chocolate", "whey"], source_url_suffix="2")

        count = detect_and_link(r2, session)
        assert count == 1

        links = session.exec(
            select(CrossLink).where(
                (CrossLink.recipe_id == r1.id) | (CrossLink.similar_to_id == r1.id)
            )
        ).all()
        assert len(links) == 1
        assert links[0].auto_generated is True

    def test_no_link_for_different_dishes(self, session):
        r1 = self._make_recipe(session, "Bolo de Chocolate",
                               ["egg", "flour", "chocolate"], source_url_suffix="3")
        r2 = self._make_recipe(session, "Frango Assado",
                               ["chicken", "garlic", "salt"], type_="savory", source_url_suffix="4")

        count = detect_and_link(r2, session)
        assert count == 0

    def test_no_link_duplicate(self, session):
        r1 = self._make_recipe(session, "Bolo", ["egg", "flour"], source_url_suffix="5")
        r2 = self._make_recipe(session, "Bolo Fit", ["egg", "flour", "whey"], source_url_suffix="6")

        # First detection
        detect_and_link(r2, session)

        # Second detection should not duplicate
        count = detect_and_link(r2, session)
        assert count == 0

    def test_respects_same_type_only(self, session):
        r1 = self._make_recipe(session, "Panqueca Doce",
                               ["egg", "flour", "sugar"], type_="sweet", source_url_suffix="7")
        r2 = self._make_recipe(session, "Panqueca Salgada",
                               ["egg", "flour", "cheese"], type_="savory", source_url_suffix="8")

        count = detect_and_link(r2, session)
        # Same dish name but different type -- should not link
        assert count == 0

    def test_respects_existing_manual_link(self, session):
        r1 = self._make_recipe(session, "Bolo", ["egg", "flour"], source_url_suffix="9")
        r2 = self._make_recipe(session, "Bolo Fit", ["egg", "flour", "whey"], source_url_suffix="10")

        # Create a manual link from r1 to r2
        manual = CrossLink(recipe_id=r1.id, similar_to_id=r2.id, auto_generated=False)
        session.add(manual)
        session.commit()

        # Auto-detection should not create a duplicate
        count = detect_and_link(r2, session)
        assert count == 0
