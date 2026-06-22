"""FastAPI application — routes, startup, dependency wiring."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import init_db

# Optional bot import — bot.py is wired in a later task
try:
    from app.bot import start_bot  # noqa: F401
except ImportError:
    start_bot = None


# ── Lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Start Telegram bot polling in background
    bot_task = None
    if start_bot is not None:
        bot_task = asyncio.create_task(start_bot())
    yield
    if bot_task is not None:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass


# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Recipe App",
    version="0.1.0",
    lifespan=lifespan,
)

static_dir = Path("app/static")
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory="app/templates")
templates.env.cache_size = 0  # work around Jinja2 3.1.6 LRUCache key hashing bug


def url_for(path: str) -> str:
    """Generate a full URL for Telegram notifications."""
    return f"http://{settings.VPS_IP}{path}"


# ── Imports to register routes ─────────────────────────────────────────────
# (imported at bottom to avoid circular deps — routes use `app` and `templates`)

from app.routes_browse import router as browse_router  # noqa: E402
from app.routes_detail import router as detail_router  # noqa: E402
from app.routes_add import router as add_router        # noqa: E402
from app.routes_import import router as import_router  # noqa: E402

app.include_router(browse_router)
app.include_router(detail_router)
app.include_router(add_router)
app.include_router(import_router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
