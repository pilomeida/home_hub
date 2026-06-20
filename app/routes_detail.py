from fastapi import APIRouter, Depends, Request
from sqlmodel import Session

from app.database import get_session
from app.models import Recipe
from app.main import templates


router = APIRouter(tags=["detail"])


@router.get("/recipe/{recipe_id}")
async def recipe_detail(recipe_id: int, request: Request, session: Session = Depends(get_session)):
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        return templates.TemplateResponse(request, "404.html", status_code=404)
    return templates.TemplateResponse(
        request, "detail.html", {"recipe": recipe}
    )
