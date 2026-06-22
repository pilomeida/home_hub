import json
import pytest
from sqlmodel import Session, select

from app.models import Recipe, CrossLink, compute_calorie_tier


class TestCalorieTier:
    def test_low_calorie(self):
        assert compute_calorie_tier(150) == "low"
        assert compute_calorie_tier(299) == "low"

    def test_mid_calorie(self):
        assert compute_calorie_tier(300) == "mid"
        assert compute_calorie_tier(599) == "mid"

    def test_high_calorie(self):
        assert compute_calorie_tier(600) == "high"
        assert compute_calorie_tier(1200) == "high"

    def test_null_calorie(self):
        assert compute_calorie_tier(None) is None


class TestRecipe:
    def test_create_recipe(self, db_session):
        recipe = Recipe(
            title="Bolo de Chocolate (whey protein)",
            dish_name="Bolo de Chocolate",
            distinguisher="whey protein",
            type="sweet",
            subtype="dessert",
            calories_per_portion=450,
            macro_tags=json.dumps(["protein-rich", "low-carb"]),
            ingredients=json.dumps(["egg", "flour", "chocolate", "whey protein"]),
            prep_time=15,
            cook_time=45,
            portions=8,
            instructions="1. Mix ingredients\n2. Bake at 180C\n3. Serve",
            source_url="https://www.instagram.com/p/example/",
        )
        db_session.add(recipe)
        db_session.commit()
        db_session.refresh(recipe)

        assert recipe.id is not None
        assert recipe.calorie_tier == "mid"
        assert recipe.total_time == 60
        assert json.loads(recipe.ingredients) == ["egg", "flour", "chocolate", "whey protein"]
        assert json.loads(recipe.macro_tags) == ["protein-rich", "low-carb"]

    def test_recipe_without_cook_time(self, db_session):
        recipe = Recipe(
            title="Salada Caesar (low-cal)",
            dish_name="Salada Caesar",
            distinguisher="low-cal",
            type="savory",
            subtype="salad",
            calories_per_portion=200,
            prep_time=10,
            cook_time=None,
            portions=2,
            source_url="https://example.com/salad",
        )
        db_session.add(recipe)
        db_session.commit()
        db_session.refresh(recipe)

        assert recipe.total_time == 10
        assert recipe.calorie_tier == "low"


class TestCrossLink:
    def test_create_cross_link(self, db_session):
        r1 = Recipe(title="Bolo Choc 1", dish_name="Bolo de Chocolate", type="sweet",
                     source_url="https://ig.com/1")
        r2 = Recipe(title="Bolo Choc 2", dish_name="Bolo de Chocolate", type="sweet",
                     source_url="https://ig.com/2")
        db_session.add_all([r1, r2])
        db_session.commit()
        db_session.refresh(r1)
        db_session.refresh(r2)

        link = CrossLink(recipe_id=r1.id, similar_to_id=r2.id, auto_generated=True)
        db_session.add(link)
        db_session.commit()

        # Query both directions
        stmt = select(CrossLink).where(
            (CrossLink.recipe_id == r1.id) | (CrossLink.similar_to_id == r1.id)
        )
        results = db_session.exec(stmt).all()
        assert len(results) == 1

    def test_cross_link_cascade_delete(self, db_session):
        r1 = Recipe(title="A", dish_name="A", type="sweet", source_url="https://ig.com/a")
        r2 = Recipe(title="B", dish_name="B", type="sweet", source_url="https://ig.com/b")
        db_session.add_all([r1, r2])
        db_session.commit()
        db_session.refresh(r1)
        db_session.refresh(r2)

        link = CrossLink(recipe_id=r1.id, similar_to_id=r2.id)
        db_session.add(link)
        db_session.commit()

        db_session.delete(r1)
        db_session.commit()

        remaining = db_session.exec(select(CrossLink)).all()
        assert len(remaining) == 0
