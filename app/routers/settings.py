"""The gear and the phone's "More" (PLAN step 10b): every page stays reachable. The gear's page shows a card per
setting with what it says today (step 10f)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.db import get_session

from app.templating import templates
from app.ui import shell

router = APIRouter(tags=["settings"])


@router.get("/settings")
async def settings(request: Request, session: Session = Depends(get_session)):
    from app.services.settings_page import settings_page
    return templates.TemplateResponse(request, "settings.html", {"links": shell.SETTINGS, "apps": shell.OTHER_APPS,
                                                                 "s": settings_page(session)})


@router.get("/more")
async def more(request: Request):
    return templates.TemplateResponse(request, "more.html", {"sections": shell.sections(), "settings": shell.SETTINGS,
                                                             "apps": shell.OTHER_APPS})


@router.get("/who")
async def choose_who(id: str = "", back: str = "/", session: Session = Depends(get_session)):
    """The Who chip (step 10i): remember who this browser is looking at, then go back. A filter for the viewer only:
    it hides nothing from anyone's login."""
    from app.services import who
    response = RedirectResponse(who.safe_back(back), status_code=303)
    person = who.chosen(session, id)
    if person is None:
        response.delete_cookie(who.COOKIE, path="/")
    else:
        response.set_cookie(who.COOKIE, str(person.id), max_age=365 * 24 * 3600, path="/", httponly=True, samesite="lax")
    return response
