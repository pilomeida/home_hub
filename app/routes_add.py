from fastapi import APIRouter, Request

router = APIRouter(tags=["add"])


@router.get("/add")
async def add_recipe_page(request: Request):
    from app.main import templates
    return templates.TemplateResponse(request, "add.html")
