"""
Fix round 3: targeted repair for 5 remaining issues.

Run on VPS:
    cd /srv/recipe-app
    /srv/recipe-app/venv/bin/python3 app/fix_photos_v3.py
"""
import asyncio
import base64
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, "/srv/recipe-app")
os.chdir("/srv/recipe-app")

from app.pdf_extractor import _save_recipe_photo_vision, _slugify, identify_inset_bbox
from app.database import engine
from app.models import Recipe
from sqlmodel import Session
from pdf2image import convert_from_path

PDF = "data/pdf_uploads/8b0bca78.pdf"
BOOK_SLUG = "broccoli-mum"
BBOX_TIMEOUT = 90

# Prompt for cards that have a two-column layout: photo LEFT, text RIGHT
LAYOUT_AWARE_PROMPT = """\
This is a recipe card page from a cookbook. The card uses a two-column layout:
- LEFT side: a real food photograph of the prepared dish
- RIGHT side: recipe text (title, ingredients, method)

Return the TIGHTEST possible bounding box for the food photograph. Rules:
- The right edge of the box MUST be before any recipe text begins
- The bottom edge MUST be before any text below the photo begins
- Exclude all white space, borders, and text from every side
- Only pixels that are actually part of the food photograph

Return JSON only, no fences:
{"has_food_photo": true, "bbox": {"x": 0.05, "y": 0.08, "w": 0.35, "h": 0.30}}

If NO food photograph exists:
{"has_food_photo": false, "bbox": null}

x, y = top-left corner; w, h = width and height — all as fractions of the page (0.0–1.0).\
"""


def _delete_photo(photo_path: str) -> None:
    p = Path(photo_path)
    if p.exists():
        p.unlink()
        print(f"    deleted {p.name}")


async def _identify_layout_aware_bbox(pdf_path: str, card_page: int) -> Optional[dict]:
    """Vision call using layout-aware prompt (photo LEFT, text RIGHT)."""
    from anthropic import AsyncAnthropic

    loop = asyncio.get_running_loop()
    images = await loop.run_in_executor(
        None,
        lambda: convert_from_path(pdf_path, first_page=card_page, last_page=card_page, dpi=150),
    )
    if not images:
        return None
    buf = io.BytesIO()
    images[0].save(buf, format="PNG")
    b64 = base64.standard_b64encode(buf.getvalue()).decode()
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
        {"type": "text", "text": LAYOUT_AWARE_PROMPT},
    ]
    client = AsyncAnthropic()
    message = await asyncio.wait_for(
        client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            temperature=0,
            messages=[{"role": "user", "content": content}],
        ),
        timeout=BBOX_TIMEOUT,
    )
    text = message.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        print(f"[layout bbox] JSON parse failed: {text[:120]!r}", flush=True)
        return None
    bbox = result.get("bbox") if result.get("has_food_photo") else None
    if bbox is not None and not isinstance(bbox, dict):
        return None
    return bbox


async def _safe_inset_bbox(pdf_path: str, card_page: int) -> Optional[dict]:
    try:
        return await asyncio.wait_for(
            identify_inset_bbox(pdf_path, card_page), timeout=BBOX_TIMEOUT
        )
    except asyncio.TimeoutError:
        print(f"[timeout after {BBOX_TIMEOUT}s]", flush=True)
        return None


async def main() -> None:
    # ── 1. Easy 2 Ingredient Dough (id=183): correct photo is page 203 ───────
    print("\n=== Fix 1: Easy 2 Ingredient Dough → page 203 ===")
    with Session(engine) as db:
        r = db.get(Recipe, 183)
        if r:
            if r.photo_path:
                _delete_photo(r.photo_path)
            new_path = await _save_recipe_photo_vision(
                PDF,
                photo_page=203,
                inset_bbox=None,
                card_page=203,
                book_slug=BOOK_SLUG,
                recipe_slug="easy-2-ingredient-dough",
            )
            r.photo_path = new_path
            db.add(r)
            db.commit()
            print(f"  saved → {new_path}")

    # ── 2. Wraps 101 (id=184): inset at bottom of page 205 ───────────────────
    print("\n=== Fix 2: Wraps 101 → inset on page 205 ===")
    with Session(engine) as db:
        r = db.get(Recipe, 184)
        if r:
            print(f"  detecting bbox on card_page=205 ...", end=" ", flush=True)
            bbox = await _safe_inset_bbox(PDF, 205)
            if bbox is None:
                print("no photo found, leaving as-is")
            else:
                print(f"bbox={bbox} → cropping")
                if r.photo_path:
                    _delete_photo(r.photo_path)
                new_path = await _save_recipe_photo_vision(
                    PDF,
                    photo_page=None,
                    inset_bbox=bbox,
                    card_page=205,
                    book_slug=BOOK_SLUG,
                    recipe_slug="wraps-101",
                )
                r.photo_path = new_path
                db.add(r)
                db.commit()
                print(f"  saved → {new_path}")

    # ── 3. Condiment re-crops with layout-aware prompt ────────────────────────
    # ids: 220 Chia Jam (card=278), 221 Garlic Mayo (card=279),
    #      229 Asian Peanut Sauce (card=288)
    print("\n=== Fix 3: Condiment re-crops (layout-aware prompt) ===")
    TARGETS = {220: 278, 221: 279, 229: 288}
    with Session(engine) as db:
        for recipe_id, card_page in TARGETS.items():
            r = db.get(Recipe, recipe_id)
            if r is None:
                print(f"  id={recipe_id}: not found")
                continue
            slug = (r.source_url.split("#", 1)[1]
                    if r.source_url and "#" in r.source_url
                    else _slugify(r.dish_name))
            print(f"  id={recipe_id} {r.dish_name!r} card_page={card_page}:", end=" ", flush=True)
            try:
                bbox = await _identify_layout_aware_bbox(PDF, card_page)
            except asyncio.TimeoutError:
                print(f"timeout, skipping")
                continue
            except Exception as exc:
                print(f"failed: {exc}")
                continue
            if bbox is None:
                print("no photo found")
                continue
            print(f"bbox={bbox} → cropping")
            if r.photo_path:
                _delete_photo(r.photo_path)
            try:
                new_path = await _save_recipe_photo_vision(
                    PDF,
                    photo_page=None,
                    inset_bbox=bbox,
                    card_page=card_page,
                    book_slug=BOOK_SLUG,
                    recipe_slug=slug,
                )
                r.photo_path = new_path
                db.add(r)
                db.commit()
            except Exception as exc:
                print(f"    photo save failed: {exc}")

    print("\n=== Done ===")


asyncio.run(main())
