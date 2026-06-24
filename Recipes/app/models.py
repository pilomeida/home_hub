"""SQLModel database models for recipes and cross-links."""

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import event
from sqlmodel import Field, Relationship, SQLModel


def compute_calorie_tier(calories: Optional[int]) -> Optional[str]:
    """Map calories per portion to a tier bucket."""
    if calories is None:
        return None
    if calories < 300:
        return "low"
    if calories < 600:
        return "mid"
    return "high"


class CrossLink(SQLModel, table=True):
    __tablename__ = "cross_links"

    recipe_id: int = Field(foreign_key="recipes.id", primary_key=True, ondelete="CASCADE")
    similar_to_id: int = Field(foreign_key="recipes.id", primary_key=True, ondelete="CASCADE")
    auto_generated: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    recipe: "Recipe" = Relationship(
        back_populates="links_from",
        sa_relationship_kwargs={"foreign_keys": "[CrossLink.recipe_id]"},
    )
    similar_to: "Recipe" = Relationship(
        back_populates="links_to",
        sa_relationship_kwargs={"foreign_keys": "[CrossLink.similar_to_id]"},
    )


class Recipe(SQLModel, table=True):
    __tablename__ = "recipes"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    dish_name: str
    distinguisher: Optional[str] = None
    type: str  # 'sweet' | 'savory'
    subtype: Optional[str] = None  # 'main'|'dessert'|'snack'|'soup'|'salad'|'breakfast'|'side'|'drink'
    calories_per_portion: Optional[int] = None
    calorie_tier: Optional[str] = None  # computed on save
    macro_tags: str = Field(default="[]")  # JSON array
    ingredients: str = Field(default="[]")  # JSON array
    prep_time: Optional[int] = None
    cook_time: Optional[int] = None
    total_time: Optional[int] = None  # computed on save
    portions: Optional[int] = None
    instructions: Optional[str] = None  # markdown
    photo_path: Optional[str] = None
    source_url: str = Field(unique=True)
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    protein_g: Optional[int] = None
    fat_g: Optional[int] = None
    carbs_g: Optional[int] = None
    fiber_g: Optional[int] = None
    cooking_types: str = Field(default="[]")  # JSON array
    source_title: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    links_from: list[CrossLink] = Relationship(
        back_populates="recipe",
        sa_relationship_kwargs={"foreign_keys": "[CrossLink.recipe_id]", "cascade": "all, delete-orphan"},
    )
    links_to: list[CrossLink] = Relationship(
        back_populates="similar_to",
        sa_relationship_kwargs={"foreign_keys": "[CrossLink.similar_to_id]", "cascade": "all, delete-orphan"},
    )

    def compute_derived_fields(self):
        """Compute calorie_tier and total_time before save."""
        if self.cook_time is not None:
            self.total_time = (self.prep_time or 0) + self.cook_time
        else:
            self.total_time = self.prep_time
        self.calorie_tier = compute_calorie_tier(self.calories_per_portion)

    @property
    def ingredients_list(self) -> list[str]:
        return json.loads(self.ingredients)

    @property
    def macro_tags_list(self) -> list[str]:
        return json.loads(self.macro_tags)

    @property
    def cooking_types_list(self) -> list[str]:
        return json.loads(self.cooking_types)

    @property
    def all_linked_ids(self) -> set[int]:
        """Return all recipe IDs linked to this recipe (both directions)."""
        ids = set()
        for link in (self.links_from or []):
            ids.add(link.similar_to_id)
        for link in (self.links_to or []):
            ids.add(link.recipe_id)
        return ids


class RecipeCreate(SQLModel):
    """Input model for creating/updating a recipe (all fields)."""
    title: str
    dish_name: str
    distinguisher: Optional[str] = None
    type: str
    subtype: Optional[str] = None
    calories_per_portion: Optional[int] = None
    macro_tags: str = "[]"
    ingredients: str = "[]"
    prep_time: Optional[int] = None
    cook_time: Optional[int] = None
    portions: Optional[int] = None
    instructions: Optional[str] = None
    photo_path: Optional[str] = None
    source_url: str
    rating: Optional[int] = None
    protein_g: Optional[int] = None
    fat_g: Optional[int] = None
    carbs_g: Optional[int] = None
    fiber_g: Optional[int] = None
    cooking_types: str = "[]"
    notes: Optional[str] = None


class RecipeUpdate(SQLModel):
    """Input model for partial updates."""
    title: Optional[str] = None
    dish_name: Optional[str] = None
    distinguisher: Optional[str] = None
    type: Optional[str] = None
    subtype: Optional[str] = None
    calories_per_portion: Optional[int] = None
    macro_tags: Optional[str] = None
    ingredients: Optional[str] = None
    prep_time: Optional[int] = None
    cook_time: Optional[int] = None
    portions: Optional[int] = None
    instructions: Optional[str] = None
    photo_path: Optional[str] = None
    rating: Optional[int] = None
    protein_g: Optional[int] = None
    fat_g: Optional[int] = None
    carbs_g: Optional[int] = None
    fiber_g: Optional[int] = None
    cooking_types: Optional[str] = None
    notes: Optional[str] = None


# Automatically compute derived fields before insert and update
@event.listens_for(Recipe, "before_insert")
@event.listens_for(Recipe, "before_update")
def _compute_recipe_derived_fields(mapper, connection, target: Recipe):
    target.compute_derived_fields()
