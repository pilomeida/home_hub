from fastapi import APIRouter, Request

router = APIRouter(tags=["browse"])


@router.get("/")
async def browse_page(request: Request):
    from app.main import templates
    return templates.TemplateResponse(request, "browse.html")
