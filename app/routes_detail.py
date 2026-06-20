"""Recipe detail route — view, rate, delete, cross-links."""

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_session
from app.models import Recipe, CrossLink
from app.main import templates

router = APIRouter(tags=["detail"])


class RatingUpdate(BaseModel):
    rating: int


@router.get("/recipe/{recipe_id}")
async def recipe_detail(
    recipe_id: int, request: Request, session: Session = Depends(get_session)
):
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        return templates.TemplateResponse(request, "404.html", {}, status_code=404)

    # Fetch linked recipes
    linked_ids = recipe.all_linked_ids
    linked_recipes = []
    if linked_ids:
        linked_recipes = session.exec(
            select(Recipe).where(Recipe.id.in_(list(linked_ids)))
        ).all()

    return templates.TemplateResponse(request, "detail.html", {
        "recipe": recipe,
        "linked_recipes": linked_recipes,
    })


@router.post("/recipe/{recipe_id}/rate")
async def rate_recipe(
    recipe_id: int,
    body: RatingUpdate,
    session: Session = Depends(get_session),
):
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        return {"ok": False}
    recipe.rating = body.rating
    recipe.updated_at = datetime.utcnow()
    session.commit()
    return {"ok": True}


@router.post("/recipe/{recipe_id}/delete")
async def delete_recipe(recipe_id: int, session: Session = Depends(get_session)):
    recipe = session.get(Recipe, recipe_id)
    if recipe:
        session.delete(recipe)
        session.commit()
    return RedirectResponse(url="/", status_code=302)
