"""Telegram bot handler -- polling, message routing, extraction pipeline call."""

import asyncio
import json
import re
import traceback

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from app.config import settings
from app.database import get_session
from app.models import Recipe
from app.scraper import fetch_content, ScrapeError
from app.extractor import extract_recipe, ExtractionError
from app.linker import detect_and_link


# -- URL detection ------------------------------------------------------------

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def is_url(text: str) -> bool:
    """Check if a message text contains a URL."""
    return bool(text) and bool(_URL_RE.search(text))


# -- Extraction lock ----------------------------------------------------------

_extraction_lock = asyncio.Lock()


# -- Bot application ----------------------------------------------------------

_tg_app: Application | None = None


async def start_bot():
    """Start the Telegram bot polling loop. Called from FastAPI lifespan."""
    global _tg_app
    _tg_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    _tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # Also handle captions (text accompanying media)
    _tg_app.add_handler(MessageHandler(filters.CAPTION & ~filters.COMMAND, handle_message))
    await _tg_app.initialize()
    await _tg_app.start()
    await _tg_app.updater.start_polling()
    # Keep running until cancelled
    while True:
        await asyncio.sleep(3600)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process incoming Telegram messages."""
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id if update.effective_user else 0
    if user_id != settings.ALLOWED_TELEGRAM_USER_ID:
        return  # silently ignore non-owner

    text = update.message.text.strip()

    if not is_url(text):
        await update.message.reply_text("Send me a recipe link!")
        return

    # Extract the URL
    url = _URL_RE.search(text).group(0)

    # Check for duplicate
    with next(get_session()) as session:
        existing = session.query(Recipe).filter_by(source_url=url).first()
        if existing:
            from app.main import url_for
            await update.message.reply_text(
                f"That recipe is already saved:\n"
                f"{existing.title}\n"
                f"{url_for(f'/recipe/{existing.id}')}"
            )
            return

    # Try to acquire lock; if busy, tell user to wait
    if _extraction_lock.locked():
        await update.message.reply_text("Still processing your previous link, one moment...")
        return

    await update.message.reply_text("Got it! Extracting recipe...")

    async with _extraction_lock:
        try:
            result = await process_extraction(url)
            if result:
                await update.message.reply_text(_success_message(result))
            else:
                await update.message.reply_text(
                    "Extraction failed for that link. "
                    f"You can add it manually at http://{settings.VPS_IP}/add"
                )
        except ScrapeError:
            await update.message.reply_text(
                "Couldn't extract that one -- the post may be private, deleted, or rate-limited."
            )
        except Exception:
            traceback.print_exc()
            await update.message.reply_text(
                "Something went wrong. Try again or add manually."
            )


# -- Extraction pipeline ------------------------------------------------------

async def process_extraction(url: str) -> dict | None:
    """Fetch, extract, store, and link a recipe from a URL.

    Returns a dict with recipe data on success, None on failure.
    """
    from app.main import url_for

    # 1. Scrape
    try:
        content = fetch_content(url)
    except Exception:
        return None

    # 2. LLM extract
    try:
        data = await extract_recipe(content.text, url)
    except ExtractionError:
        return None

    # 3. Build title
    dish_name = data.get("dish_name") or "Unknown"
    distinguisher = data.get("distinguishing_feature")
    title = f"{dish_name} ({distinguisher})" if distinguisher else dish_name

    # 4. Insert into DB
    recipe = Recipe(
        title=title,
        dish_name=dish_name,
        distinguisher=distinguisher,
        type=data.get("type", "sweet"),
        subtype=data.get("subtype"),
        calories_per_portion=data.get("calories_per_portion"),
        macro_tags=json.dumps(data.get("macro_tags") or []),
        ingredients=json.dumps(data.get("ingredients") or []),
        prep_time=data.get("prep_time_minutes"),
        cook_time=data.get("cook_time_minutes"),
        portions=data.get("portions"),
        instructions=data.get("instructions"),
        photo_path=content.image_path,
        source_url=url,
    )
    recipe.compute_derived_fields()

    with next(get_session()) as session:
        session.add(recipe)
        session.commit()
        session.refresh(recipe)

        # 5. Cross-link
        detect_and_link(recipe, session)

        recipe_id = recipe.id
        return {
            "title": recipe.title,
            "id": recipe_id,
            "calorie_tier": recipe.calorie_tier,
            "macro_tags": json.loads(recipe.macro_tags),
            "total_time": recipe.total_time,
            "url": url_for(f"/recipe/{recipe_id}"),
        }


def _success_message(result: dict) -> str:
    """Format the success notification for Telegram."""
    tier = result.get("calorie_tier", "?")
    tier_emoji = {"low": "green", "mid": "yellow", "high": "red"}.get(tier, "white")
    tags = ", ".join(result.get("macro_tags", [])[:2])
    time_str = f"{result.get('total_time', '?')}min" if result.get("total_time") else "? min"

    lines = [
        f"{result['title']} added!",
        f"{time_str} -- {tier_emoji} {tier} cal",
    ]
    if tags:
        lines.append(f"Tags: {tags}")
    lines.append(f"View: {result['url']}")
    return "\n".join(lines)
