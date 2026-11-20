"""The gear and the phone's "More" (PLAN step 10b): two plain pages of links, so every page stays reachable."""

from fastapi import APIRouter, Request

from app.templating import templates
from app.ui import shell

router = APIRouter(tags=["settings"])


@router.get("/settings")
async def settings(request: Request):
    return templates.TemplateResponse(request, "settings.html", {"links": shell.SETTINGS, "apps": shell.OTHER_APPS})


@router.get("/more")
async def more(request: Request):
    return templates.TemplateResponse(request, "more.html", {"sections": shell.sections(), "settings": shell.SETTINGS,
                                                             "apps": shell.OTHER_APPS})
