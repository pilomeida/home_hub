"""FastAPI application — routes, startup, dependency wiring."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth import CloudflareAccessMiddleware
from app.config import settings

app = FastAPI(title="Home & Family Hub", version="0.1.0")

if settings.CF_ACCESS_TEAM_DOMAIN:
    app.add_middleware(CloudflareAccessMiddleware)

static_dir = Path("app/static")
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

from app.routers import (  # noqa: E402
    ask, bank, bills, dashboard, documents, health, house, inbox, insurance, loans, privacy, problems, schedules, to_check, todos, transactions, utilities, wiki, wiki_lint,
)

app.include_router(dashboard.router)
app.include_router(ask.router)
app.include_router(bank.router)
app.include_router(bills.router)
app.include_router(documents.router)
app.include_router(health.router)
app.include_router(house.router)
app.include_router(insurance.router)
app.include_router(inbox.router)
app.include_router(loans.router)
app.include_router(privacy.router)
app.include_router(schedules.router)
app.include_router(problems.router)
app.include_router(to_check.router)
app.include_router(todos.router)
app.include_router(transactions.router)
app.include_router(utilities.router)
app.include_router(wiki_lint.router)
app.include_router(wiki.router)


from fastapi import HTTPException, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

from app.privacy.settings import TierHeld  # noqa: E402
from app.problems import Problem, for_http, render_html  # noqa: E402
from app.templating import templates  # noqa: E402


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def _problem_response(request: Request, problem: Problem, status: int, technical: str | None = None,
                      api_detail: object = None):
    """One place that decides how a failure is shown: a full page for a browser, a banner fragment for
    HTMX (so a failed button press is never silent), and the original JSON for API-style callers."""
    if request.headers.get("hx-request"):
        return HTMLResponse(f'<div class="problem" role="alert">{render_html(problem, technical)}</div>', status_code=status,
                            headers={"HX-Retarget": "#problem-banner", "HX-Reswap": "innerHTML"})
    if _wants_html(request):
        return templates.TemplateResponse(request, "problems/error.html",
                                          {"problem": problem, "technical": technical}, status_code=status)
    return JSONResponse({"detail": api_detail if api_detail is not None else problem.text}, status_code=status)


@app.exception_handler(StarletteHTTPException)
async def _http_problem(request: Request, exc: StarletteHTTPException):
    return _problem_response(request, for_http(exc.status_code, exc.detail), exc.status_code,
                             api_detail=exc.detail)


@app.exception_handler(TierHeld)
async def _tier_held(request: Request, exc: TierHeld):
    problem = Problem("Not uploaded.", "This kind of document has no privacy level yet, so nothing may read it.",
                      "Set its level on the Privacy page, then try again. Nothing was stored.", "/privacy")
    if not request.headers.get("hx-request") and not _wants_html(request):
        return HTMLResponse(f"<p>{render_html(problem)}</p>", status_code=409)
    return _problem_response(request, problem, 409)
