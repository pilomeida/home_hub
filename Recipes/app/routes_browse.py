"""Browse route — faceted filter sidebar + recipe grid."""

import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import Session, select

from app.database import get_session
from app.models import Recipe
from app.main import templates, url_for

_OPTIONAL_RE = re.compile(r'^\(optional\)\s*', re.IGNORECASE)
_PAREN_RE    = re.compile(r'\s*\([^)]*\)')
_QTY_RE      = re.compile(
    r'^[½¼¾⅓⅔⅛⅜⅝⅞\d./\s]+'
    r'(?:g|ml|mL|l|L|kg|lb|oz|tbsp|tsp|tablespoons?|teaspoons?|cups?'
    r'|pieces?|slices?|cloves?|pinch|handful|drops?|sprigs?|scoops?|cans?|squares?)?'
    r'\s*',
    re.IGNORECASE,
)
_INDEF_RE    = re.compile(r'^(?:a few \w+|a handful|some|an?)\s+(?:of\s+)?', re.IGNORECASE)
_OF_RE       = re.compile(r'^of\s+', re.IGNORECASE)
_PREP_RE     = re.compile(
    r'^(?:cooked\s+and\s+drained|chopped|diced|sliced|minced|crushed|grated|'
    r'shredded|ground|roasted|toasted|dried|frozen|canned|ripe|powdered)\s+',
    re.IGNORECASE,
)
_TO_TASTE_RE = re.compile(r'\s+to\s+taste\s*$', re.IGNORECASE)


def _norm_ingredient(raw: str) -> str:
    s = raw.strip()
    s = _OPTIONAL_RE.sub('', s)
    s = _PAREN_RE.sub('', s).strip()
    s = _INDEF_RE.sub('', s)
    s = _QTY_RE.sub('', s)
    s = _OF_RE.sub('', s)
    s = _PREP_RE.sub('', s)
    s = _TO_TASTE_RE.sub('', s)
    s = s.strip(' ,.-')
    return s.capitalize() if s else raw.capitalize()

router = APIRouter(tags=["browse"])


@router.get("/")
async def browse_page(
    request: Request,
    type: Optional[list[str]] = Query(default=None, alias="type"),
    subtype: Optional[list[str]] = Query(default=None, alias="subtype"),
    macro: Optional[list[str]] = Query(default=None, alias="macro"),
    calorie_tier: Optional[list[str]] = Query(default=None, alias="calorie_tier"),
    ingredient: Optional[list[str]] = Query(default=None, alias="ingredient"),
    max_time: Optional[int] = Query(default=None, alias="max_time"),
    min_rating: Optional[int] = Query(default=None, alias="min_rating", ge=1, le=5),
    cooking_type: Optional[list[str]] = Query(default=None, alias="cooking_type"),
    session: Session = Depends(get_session),
):
    # Build base query
    query = select(Recipe)

    if type:
        query = query.where(Recipe.type.in_(type))
    if subtype:
        query = query.where(Recipe.subtype.in_(subtype))
    if calorie_tier:
        query = query.where(Recipe.calorie_tier.in_(calorie_tier))
    if max_time is not None:
        query = query.where(Recipe.total_time <= max_time)
    if min_rating is not None:
        query = query.where(Recipe.rating >= min_rating)

    recipes = session.exec(query.order_by(Recipe.created_at.desc())).all()

    # Post-query filtering (JSON fields)
    if macro:
        recipes = [r for r in recipes if any(m in r.macro_tags_list for m in macro)]
    if ingredient:
        recipes = [
            r for r in recipes
            if all(
                any(_norm_ingredient(raw) == ing for raw in r.ingredients_list)
                for ing in ingredient
            )
        ]
    if cooking_type:
        recipes = [r for r in recipes if any(ct in r.cooking_types_list for ct in cooking_type)]

    # Build filter option lists from ALL recipes in DB (not just filtered)
    all_recipes = session.exec(select(Recipe)).all()
    all_types: set[str] = set()
    all_subtypes: set[str] = set()
    all_macros: set[str] = set()
    all_ingredients: set[str] = set()
    all_tiers: set[str] = set()
    all_cooking_types: set[str] = set()

    for r in all_recipes:
        all_types.add(r.type)
        if r.subtype:
            all_subtypes.add(r.subtype)
        for m in r.macro_tags_list:
            all_macros.add(m)
        for ing in r.ingredients_list:
            all_ingredients.add(_norm_ingredient(ing))
        if r.calorie_tier:
            all_tiers.add(r.calorie_tier)
        for ct in r.cooking_types_list:
            all_cooking_types.add(ct)

    return templates.TemplateResponse(request, "browse.html", {
        "recipes": recipes,
        "filters": {
            "types": sorted(all_types),
            "subtypes": sorted(all_subtypes),
            "macros": sorted(all_macros),
            "ingredients": sorted(all_ingredients),
            "calorie_tiers": sorted(all_tiers),
            "cooking_types": sorted(all_cooking_types),
        },
        "active": {
            "type": type or [],
            "subtype": subtype or [],
            "macro": macro or [],
            "calorie_tier": calorie_tier or [],
            "ingredient": ingredient or [],
            "max_time": max_time,
            "min_rating": min_rating,
            "cooking_type": cooking_type or [],
        },
    })
