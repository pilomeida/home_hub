"""Telegram bot handler -- polling, message routing, extraction pipeline call."""

import asyncio
import hashlib
import json
import traceback
from pathlib import Path

from telegram import Update, PhotoSize
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from app.config import settings
from app.database import get_session
from app.models import Recipe
from app.scraper import fetch_content
from app.extractor import extract_recipe, extract_recipe_from_images, ExtractionError
from app.linker import detect_and_link


# -- Extraction lock ----------------------------------------------------------

_extraction_lock = asyncio.Lock()


# -- Media-group buffer (collect album photos before processing) --------------

_media_group_buffers: dict[str, list[PhotoSize]] = {}
_media_group_updates: dict[str, Update] = {}   # first update per group for replies
_media_group_tasks: dict[str, asyncio.Task] = {}


# -- Bot application ----------------------------------------------------------

_tg_app: Application | None = None


async def start_bot():
    """Start the Telegram bot polling loop. Called from FastAPI lifespan."""
    global _tg_app
    _tg_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    _tg_app.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_photo))
    _tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    await _tg_app.initialize()
    await _tg_app.start()
    await _tg_app.updater.start_polling()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await _tg_app.updater.stop()
        await _tg_app.stop()
        await _tg_app.shutdown()


# -- Photo handler ------------------------------------------------------------

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buffer incoming photo messages; process once the album is complete."""
    msg = update.message
    if not msg or not msg.photo:
        return

    user_id = update.effective_user.id if update.effective_user else 0
    if user_id != settings.ALLOWED_TELEGRAM_USER_ID:
        return

    group_id = msg.media_group_id or str(msg.message_id)

    if group_id not in _media_group_buffers:
        _media_group_buffers[group_id] = []
        _media_group_updates[group_id] = update

    _media_group_buffers[group_id].append(msg.photo[-1])  # largest resolution

    # Reset 2-second timer on each new photo in the group
    if group_id in _media_group_tasks:
        _media_group_tasks[group_id].cancel()
    _media_group_tasks[group_id] = asyncio.create_task(
        _process_group_after_delay(context, group_id, delay=2.0)
    )


async def _process_group_after_delay(
    context: ContextTypes.DEFAULT_TYPE, group_id: str, delay: float
):
    await asyncio.sleep(delay)
    photos = _media_group_buffers.pop(group_id, [])
    update = _media_group_updates.pop(group_id, None)
    _media_group_tasks.pop(group_id, None)

    if not update or not photos:
        return

    if len(photos) < 2:
        await update.message.reply_text(
            "Send at least 2 photos: recipe screenshot(s) first, food photo of the dish last."
        )
        return

    if _extraction_lock.locked():
        await update.message.reply_text("Still processing, one moment…")
        return

    await update.message.reply_text("Got it, extracting from your photos…")

    async with _extraction_lock:
        source_url = f"tg://img/{group_id}"

        # Duplicate check
        session_gen = get_session()
        session = next(session_gen)
        try:
            existing = session.query(Recipe).filter_by(source_url=source_url).first()
            if existing:
                from app.main import url_for
                await update.message.reply_text(
                    f"Already saved: {existing.title}\n"
                    f"{url_for(f'/recipe/{existing.id}')}"
                )
                return
        finally:
            session.close()

        try:
            # Download all photos
            all_bytes: list[bytes] = []
            for photo in photos:
                tg_file = await context.bot.get_file(photo.file_id)
                data = await tg_file.download_as_bytearray()
                all_bytes.append(bytes(data))

            recipe_image_bytes = all_bytes[:-1]   # screenshots
            food_photo_bytes = all_bytes[-1]       # last = food photo

            # Extract recipe from screenshots
            data = await extract_recipe_from_images(recipe_image_bytes)

            # Save food photo
            dish_name = data.get("dish_name") or "recipe"
            slug = hashlib.md5(source_url.encode()).hexdigest()[:12]
            photo_filename = f"tg-{slug}.jpg"
            photo_path = Path(settings.PHOTOS_DIR) / photo_filename
            photo_path.parent.mkdir(parents=True, exist_ok=True)
            photo_path.write_bytes(food_photo_bytes)

            # Build and store recipe
            result = await _store_recipe(data, str(photo_path), source_url)
            await update.message.reply_text(_success_message(result))

        except ExtractionError:
            await update.message.reply_text(
                f"Couldn't read the recipe from those images. "
                f"Try again or add it manually at http://{settings.VPS_IP}/add"
            )
        except Exception:
            traceback.print_exc()
            await update.message.reply_text("Something went wrong. Try again or add manually.")


# -- URL handler (generic websites only) --------------------------------------

_URL_RE = __import__("re").compile(r"https?://\S+", __import__("re").IGNORECASE)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process incoming text messages containing a URL."""
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id if update.effective_user else 0
    if user_id != settings.ALLOWED_TELEGRAM_USER_ID:
        return

    text = update.message.text.strip()
    m = _URL_RE.search(text)
    if not m:
        await update.message.reply_text(
            "Send me a recipe URL, or send photos (recipe screenshot(s) + food photo last)."
        )
        return

    url = m.group(0)

    if _extraction_lock.locked():
        await update.message.reply_text("Still processing, one moment…")
        return

    await update.message.reply_text("Got it! Extracting recipe…")

    async with _extraction_lock:
        session_gen = get_session()
        session = next(session_gen)
        try:
            existing = session.query(Recipe).filter_by(source_url=url).first()
            if existing:
                from app.main import url_for
                await update.message.reply_text(
                    f"Already saved: {existing.title}\n"
                    f"{url_for(f'/recipe/{existing.id}')}"
                )
                return
        finally:
            session.close()

        try:
            result = await process_extraction(url)
            if result:
                await update.message.reply_text(_success_message(result))
            else:
                await update.message.reply_text(
                    f"Extraction failed. Add manually at http://{settings.VPS_IP}/add"
                )
        except Exception:
            traceback.print_exc()
            await update.message.reply_text("Something went wrong. Try again or add manually.")


# -- Extraction pipeline (URL path) -------------------------------------------

async def process_extraction(url: str) -> dict | None:
    """Fetch, extract, store, and link a recipe from a URL."""
    try:
        content = fetch_content(url)
    except Exception:
        return None

    try:
        data = await extract_recipe(content.text, url)
    except ExtractionError:
        return None

    return await _store_recipe(data, content.image_path, url)


# -- Shared storage logic -----------------------------------------------------

async def _store_recipe(data: dict, photo_path: str | None, source_url: str) -> dict:
    """Insert recipe into DB, run cross-linker, return summary dict."""
    from app.main import url_for

    dish_name = data.get("dish_name") or "Unknown"
    distinguisher = data.get("distinguishing_feature")
    title = f"{dish_name} ({distinguisher})" if distinguisher else dish_name

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
        protein_g=data.get("protein_g"),
        fat_g=data.get("fat_g"),
        carbs_g=data.get("carbs_g"),
        fiber_g=data.get("fiber_g"),
        cooking_types=json.dumps(data.get("cooking_types") or []),
        photo_path=photo_path,
        source_url=source_url,
        source_title="Telegram" if source_url.startswith("tg://") else None,
    )
    session_gen = get_session()
    session = next(session_gen)
    try:
        session.add(recipe)
        session.commit()
        session.refresh(recipe)
        detect_and_link(recipe, session)
        return {
            "title": recipe.title,
            "id": recipe.id,
            "calorie_tier": recipe.calorie_tier,
            "macro_tags": json.loads(recipe.macro_tags),
            "total_time": recipe.total_time,
            "url": url_for(f"/recipe/{recipe.id}"),
        }
    finally:
        session.close()


def _success_message(result: dict) -> str:
    """Format the success notification for Telegram."""
    tier = result.get("calorie_tier", "?")
    tier_label = {"low": "🟢 low", "mid": "🟡 mid", "high": "🔴 high"}.get(tier, tier)
    tags = ", ".join(result.get("macro_tags", [])[:2])
    time_str = f"{result.get('total_time')}min" if result.get("total_time") else "? min"

    lines = [
        f"{result['title']} added!",
        f"{time_str} · {tier_label} cal",
    ]
    if tags:
        lines.append(f"Tags: {tags}")
    lines.append(f"View: {result['url']}")
    return "\n".join(lines)
