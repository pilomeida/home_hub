"""Cross-link detector -- finds similar recipes by ingredient and name overlap."""

import json
from difflib import SequenceMatcher

from sqlmodel import Session, select

from app.models import CrossLink, Recipe


def jaccard_similarity(a: set, b: set) -> float:
    """Compute Jaccard similarity between two sets. Returns 0.0 for empty sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _levenshtein_ratio(a: str, b: str) -> float:
    """Wrapper around SequenceMatcher for a float ratio [0.0, 1.0]."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


_THRESHOLD = 0.6


def detect_and_link(recipe: Recipe, session: Session) -> int:
    """Detect similar recipes and create cross-links.

    Compares the given recipe against all existing recipes of the same type.
    Creates links if ingredient Jaccard OR dish-name Levenshtein >= 0.6.

    Skips pairs where a link already exists (auto or manual).

    Returns the number of new cross-links created.
    """
    ingredients_a = set(json.loads(recipe.ingredients)) if recipe.ingredients else set()
    name_a = recipe.dish_name or ""

    existing_ids = recipe.all_linked_ids
    existing_ids.add(recipe.id or 0)

    # Find candidates: same type, not already linked
    candidates = session.exec(
        select(Recipe).where(
            Recipe.type == recipe.type,
            Recipe.id.notin_(list(existing_ids)),
        )
    ).all()

    new_count = 0
    for candidate in candidates:
        if candidate.id == recipe.id:
            continue

        ingredients_b = set(json.loads(candidate.ingredients)) if candidate.ingredients else set()
        name_b = candidate.dish_name or ""

        ing_score = jaccard_similarity(ingredients_a, ingredients_b)
        name_score = _levenshtein_ratio(name_a, name_b)

        if ing_score >= _THRESHOLD or name_score >= _THRESHOLD:
            # Bidirectional row
            link = CrossLink(
                recipe_id=recipe.id,
                similar_to_id=candidate.id,
                auto_generated=True,
            )
            session.add(link)
            new_count += 1

    if new_count > 0:
        session.commit()

    return new_count
