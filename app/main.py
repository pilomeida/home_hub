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
    ask, bank, bills, dashboard, documents, house, inbox, insurance, loans, privacy, todos, transactions, utilities, wiki, wiki_lint,
)

app.include_router(dashboard.router)
app.include_router(ask.router)
app.include_router(bank.router)
app.include_router(bills.router)
app.include_router(documents.router)
app.include_router(house.router)
app.include_router(insurance.router)
app.include_router(inbox.router)
app.include_router(loans.router)
app.include_router(privacy.router)
app.include_router(todos.router)
app.include_router(transactions.router)
app.include_router(utilities.router)
app.include_router(wiki_lint.router)
app.include_router(wiki.router)


from fastapi import Request  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402

from app.privacy.settings import TierHeld  # noqa: E402


@app.exception_handler(TierHeld)
async def _tier_held(request: Request, exc: TierHeld):
    return HTMLResponse(
        "<p>This kind of document has no privacy level yet, so nothing can read it. "
        "<a href='/privacy'>Set its level on the Privacy page</a> and try again. Nothing was uploaded.</p>",
        status_code=409)
