"""FastAPI application — routes, startup, dependency wiring."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth import CloudflareAccessMiddleware
from app.config import settings
from app.routers import bills, todos

app = FastAPI(title="Home & Family Hub", version="0.1.0")

if settings.CF_ACCESS_TEAM_DOMAIN:
    app.add_middleware(CloudflareAccessMiddleware)

static_dir = Path("app/static")
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory="app/templates")
templates.env.cache_size = 0


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def dashboard_placeholder(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"data": None})


app.include_router(bills.router)
app.include_router(todos.router)
