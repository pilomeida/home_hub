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


def test_recipe_new_macro_fields():
    r = Recipe(
        title="T", dish_name="T", type="savory",
        source_url="pdf:book#recipe",
        protein_g=30, fat_g=10, carbs_g=40, fiber_g=5,
        cooking_types='["oven", "cooktop"]',
    )
    assert r.protein_g == 30
    assert r.fat_g == 10
    assert r.carbs_g == 40
    assert r.fiber_g == 5
    assert r.cooking_types_list == ["oven", "cooktop"]


def test_recipe_macro_fields_nullable():
    r = Recipe(title="T", dish_name="T", type="savory", source_url="pdf:b#r")
    assert r.protein_g is None
    assert r.fiber_g is None
    assert r.cooking_types_list == []


def test_recipe_create_accepts_new_fields():
    from app.models import RecipeCreate
    rc = RecipeCreate(
        title="T", dish_name="T", type="savory",
        source_url="pdf:book#recipe",
        protein_g=25, fat_g=8, carbs_g=30, fiber_g=None,
        cooking_types='["microwave"]',
    )
    assert rc.protein_g == 25
    assert rc.cooking_types == '["microwave"]'


def test_migrate_db_adds_new_columns(tmp_path):
    from sqlalchemy import create_engine, text
    from app.database import migrate_db

    # Build a DB with the OLD schema (no new columns)
    old_engine = create_engine(f"sqlite:///{tmp_path}/old.db")
    with old_engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE recipes ("
            "id INTEGER PRIMARY KEY, title TEXT, dish_name TEXT, "
            "type TEXT, source_url TEXT UNIQUE, "
            "macro_tags TEXT DEFAULT '[]', ingredients TEXT DEFAULT '[]', "
            "created_at TEXT, updated_at TEXT"
            ")"
        ))
        conn.commit()

    migrate_db(old_engine)

    with old_engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(recipes)"))}

    assert {"protein_g", "fat_g", "carbs_g", "fiber_g", "cooking_types"} <= cols


def test_recipe_notes_field():
    from app.models import Recipe
    r = Recipe(
        title="Brownie", dish_name="Brownie", type="sweet",
        source_url="pdf:test#brownie",
        notes="If you love brownie batter, this is for you.",
    )
    assert r.notes == "If you love brownie batter, this is for you."


def test_recipe_notes_defaults_to_none():
    from app.models import Recipe
    r = Recipe(title="X", dish_name="X", type="savory", source_url="pdf:b#x")
    assert r.notes is None


def test_recipe_create_accepts_notes():
    from app.models import RecipeCreate
    rc = RecipeCreate(
        title="X", dish_name="X", type="savory",
        source_url="pdf:b#x",
        notes="Some intro text.",
    )
    assert rc.notes == "Some intro text."


def test_migrate_db_adds_source_title_and_notes(tmp_path):
    from sqlalchemy import create_engine, text
    from app.database import migrate_db

    old_engine = create_engine(f"sqlite:///{tmp_path}/old.db")
    with old_engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE recipes ("
            "id INTEGER PRIMARY KEY, title TEXT, dish_name TEXT, "
            "type TEXT, source_url TEXT UNIQUE, "
            "macro_tags TEXT DEFAULT '[]', ingredients TEXT DEFAULT '[]', "
            "created_at TEXT, updated_at TEXT"
            ")"
        ))
        conn.commit()

    migrate_db(old_engine)

    with old_engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(recipes)"))}

    assert "source_title" in cols
    assert "notes" in cols
