"""Add and Edit recipe routes -- URL fetch + manual entry."""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app.database import get_session
from app.models import Recipe
from app.scraper import fetch_content
from app.extractor import extract_recipe
from app.linker import detect_and_link
from app.main import templates

router = APIRouter(tags=["add"])


# -- Add Recipe ------------------------------------------------------------

@router.get("/add")
async def add_recipe_page(request: Request):
    return templates.TemplateResponse(request, "add.html", {
        "recipe": None,  # None = create mode
        "error": None,
    })


@router.post("/add/fetch")
async def fetch_from_url(request: Request):
    """POST endpoint: given a URL, scrape + LLM-extract and return JSON.
    Does NOT save to DB -- returns data for the form to pre-fill.
    """
    form = await request.form()
    url = form.get("url", "").strip()
    if not url:
        return JSONResponse({"error": "No URL provided"}, status_code=400)

    try:
        content = fetch_content(url)
    except Exception as e:
        return JSONResponse({"error": f"Could not fetch URL: {e}"}, status_code=400)

    try:
        data = await extract_recipe(content.text, url)
    except Exception as e:
        return JSONResponse({"error": f"Extraction failed: {e}"}, status_code=422)

    # Return as form-prefill data
    return {
        "dish_name": data.get("dish_name"),
        "distinguisher": data.get("distinguishing_feature"),
        "type": data.get("type"),
        "subtype": data.get("subtype"),
        "calories_per_portion": data.get("calories_per_portion"),
        "macro_tags": json.dumps(data.get("macro_tags") or []),
        "ingredients": json.dumps(data.get("ingredients") or []),
        "prep_time": data.get("prep_time_minutes"),
        "cook_time": data.get("cook_time_minutes"),
        "portions": data.get("portions"),
        "instructions": data.get("instructions"),
        "source_url": url,
        "photo_path": content.image_path,
    }


@router.post("/add")
async def save_recipe(
    request: Request,
    title: str = Form(...),
    dish_name: str = Form(...),
    distinguisher: str = Form(default=""),
    type: str = Form(...),
    subtype: str = Form(default=""),
    calories_per_portion: str = Form(default=""),
    macro_tags: str = Form(default="[]"),
    ingredients: str = Form(default="[]"),
    prep_time: str = Form(default=""),
    cook_time: str = Form(default=""),
    portions: str = Form(default=""),
    instructions: str = Form(default=""),
    source_url: str = Form(...),
    photo_path: str = Form(default=""),
    rating: str = Form(default=""),
    protein_g: str = Form(default=""),
    fat_g: str = Form(default=""),
    carbs_g: str = Form(default=""),
    fiber_g: str = Form(default=""),
    cooking_types: str = Form(default="[]"),
    session: Session = Depends(get_session),
):
    # Check duplicate
    existing = session.exec(select(Recipe).where(Recipe.source_url == source_url)).first()
    if existing:
        return templates.TemplateResponse(request, "add.html", {
            "recipe": None,
            "error": f"A recipe from this URL already exists: /recipe/{existing.id}",
        })

    recipe = Recipe(
        title=title,
        dish_name=dish_name,
        distinguisher=distinguisher or None,
        type=type,
        subtype=subtype or None,
        calories_per_portion=int(calories_per_portion) if calories_per_portion else None,
        macro_tags=macro_tags,
        ingredients=ingredients,
        prep_time=int(prep_time) if prep_time else None,
        cook_time=int(cook_time) if cook_time else None,
        portions=int(portions) if portions else None,
        instructions=instructions or None,
        source_url=source_url,
        photo_path=photo_path or None,
        rating=int(rating) if rating else None,
        protein_g=int(protein_g) if protein_g else None,
        fat_g=int(fat_g) if fat_g else None,
        carbs_g=int(carbs_g) if carbs_g else None,
        fiber_g=int(fiber_g) if fiber_g else None,
        cooking_types=cooking_types,
    )
    recipe.compute_derived_fields()

    session.add(recipe)
    session.commit()
    session.refresh(recipe)

    detect_and_link(recipe, session)

    return RedirectResponse(url=f"/recipe/{recipe.id}", status_code=302)


# -- Edit Recipe ------------------------------------------------------------

@router.get("/edit/{recipe_id}")
async def edit_recipe_page(recipe_id: int, request: Request, session: Session = Depends(get_session)):
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        return templates.TemplateResponse(request, "404.html", {}, status_code=404)
    return templates.TemplateResponse(request, "add.html", {
        "recipe": recipe,  # not None = edit mode
        "error": None,
    })


@router.post("/edit/{recipe_id}")
async def update_recipe(
    recipe_id: int,
    request: Request,
    title: str = Form(...),
    dish_name: str = Form(...),
    distinguisher: str = Form(default=""),
    type: str = Form(...),
    subtype: str = Form(default=""),
    calories_per_portion: str = Form(default=""),
    macro_tags: str = Form(default="[]"),
    ingredients: str = Form(default="[]"),
    prep_time: str = Form(default=""),
    cook_time: str = Form(default=""),
    portions: str = Form(default=""),
    instructions: str = Form(default=""),
    source_url: str = Form(...),
    photo_path: str = Form(default=""),
    rating: str = Form(default=""),
    protein_g: str = Form(default=""),
    fat_g: str = Form(default=""),
    carbs_g: str = Form(default=""),
    fiber_g: str = Form(default=""),
    cooking_types: str = Form(default="[]"),
    session: Session = Depends(get_session),
):
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        return templates.TemplateResponse(request, "404.html", {}, status_code=404)

    # Check for duplicate source_url (if changed)
    if source_url != recipe.source_url:
        existing = session.exec(
            select(Recipe).where(Recipe.source_url == source_url)
        ).first()
        if existing:
            return templates.TemplateResponse("add.html", {
                "request": request,
                "recipe": recipe,
                "error": f"Another recipe already uses this URL: /recipe/{existing.id}",
            })

    recipe.title = title
    recipe.dish_name = dish_name
    recipe.distinguisher = distinguisher or None
    recipe.type = type
    recipe.subtype = subtype or None
    recipe.calories_per_portion = int(calories_per_portion) if calories_per_portion else None
    recipe.macro_tags = macro_tags
    recipe.ingredients = ingredients
    recipe.prep_time = int(prep_time) if prep_time else None
    recipe.cook_time = int(cook_time) if cook_time else None
    recipe.portions = int(portions) if portions else None
    recipe.instructions = instructions or None
    recipe.source_url = source_url
    if photo_path:
        recipe.photo_path = photo_path
    if rating:
        recipe.rating = int(rating)
    recipe.protein_g = int(protein_g) if protein_g else None
    recipe.fat_g = int(fat_g) if fat_g else None
    recipe.carbs_g = int(carbs_g) if carbs_g else None
    recipe.fiber_g = int(fiber_g) if fiber_g else None
    recipe.cooking_types = cooking_types
    recipe.updated_at = datetime.utcnow()

    recipe.compute_derived_fields()
    session.commit()
    session.refresh(recipe)

    # Re-run cross-link detection (ingredients/dish may have changed)
    detect_and_link(recipe, session)

    return RedirectResponse(url=f"/recipe/{recipe.id}", status_code=302)
