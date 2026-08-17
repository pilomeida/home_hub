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

from app.routers import bills, dashboard, todos, wiki  # noqa: E402

app.include_router(dashboard.router)
app.include_router(bills.router)
app.include_router(todos.router)
app.include_router(wiki.router)
