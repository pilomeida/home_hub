"""
One-time script to fix Broccoli Mum recipe photos.

Phase 1: Deduplication — clear photo_path for the losing recipe in each
         duplicate photo_page claim.
Phase 2: Inset repair — for each recipe whose photo is a full card page,
         detect the real food photo bbox and re-crop, or null the photo out
         if no real photo exists.

Run on VPS:
    cd /srv/recipe-app
    /srv/recipe-app/venv/bin/python3 app/fix_photos.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/srv/recipe-app")
os.chdir("/srv/recipe-app")

from app.pdf_extractor import (
    _save_recipe_photo_vision,
    _slugify,
    extract_toc_vision,
    identify_inset_bbox,
)
from app.database import engine
from app.models import Recipe
from sqlmodel import Session

PDF = "data/pdf_uploads/8b0bca78.pdf"
BOOK_SLUG = "broccoli-mum"

# Phase 1: recipe ids whose photo belongs to a different recipe
DEDUP_LOSERS = [182, 190]

# Phase 2: recipe ids that have a full-card-page photo saved instead of a real photo.
# Values are card page numbers from the main session JSON.
SESSION_INSETS: dict[int, int] = {
    116: 64,   # 5 Minute Blueberry Crumble
    117: 65,   # Mango Sticky Rice
    121: 72,   # Chocolate Lava Cake
    122: 73,   # Cookie Dough Milkshake
    192: 221,  # Cheesy Crispy Beans
}
CONDIMENT_IDS = list(range(218, 233))  # ids 218–232


def _delete_photo(photo_path: str) -> None:
    p = Path(photo_path)
    if p.exists():
        p.unlink()
        print(f"    deleted {p.name}")
    else:
        print(f"    file not found (already gone): {p.name}")


async def main() -> None:
    # ── Phase 1: Deduplication ──────────────────────────────────────────────────
    print("\n=== Phase 1: Deduplication ===")
    with Session(engine) as db:
        for recipe_id in DEDUP_LOSERS:
            r = db.get(Recipe, recipe_id)
            if r is None:
                print(f"  id={recipe_id}: not found, skipping")
                continue
            if r.photo_path is None:
                print(f"  id={recipe_id} {r.dish_name!r}: already null — skip")
                continue
            print(f"  id={recipe_id} {r.dish_name!r}: clearing photo")
            _delete_photo(r.photo_path)
            r.photo_path = None
            db.add(r)
            db.commit()

    # ── Phase 2: Inset repair ───────────────────────────────────────────────────
    print("\n=== Phase 2: Inset repair ===")

    # Resolve card pages for condiment recipes via the full TOC
    print("  Fetching full TOC (pages 3–16) for condiment card pages…")
    full_toc = await extract_toc_vision(PDF, toc_page_range=(3, 16))
    toc_by_slug: dict[str, int] = {_slugify(r["recipe_title"]): r["page"] for r in full_toc}
    print(f"  TOC loaded: {len(full_toc)} entries")

    inset_card_pages: dict[int, int] = dict(SESSION_INSETS)

    with Session(engine) as db:
        for recipe_id in CONDIMENT_IDS:
            r = db.get(Recipe, recipe_id)
            if r is None:
                continue
            if r.source_url and "#" in r.source_url:
                slug = r.source_url.split("#", 1)[1]
            else:
                slug = _slugify(r.dish_name)
            page = toc_by_slug.get(slug) or toc_by_slug.get(_slugify(r.dish_name))
            if page is None:
                print(f"  WARN: no TOC entry for id={recipe_id} {r.dish_name!r}")
                continue
            inset_card_pages[recipe_id] = page

    # Process each inset recipe
    with Session(engine) as db:
        for recipe_id, card_page in sorted(inset_card_pages.items()):
            r = db.get(Recipe, recipe_id)
            if r is None:
                print(f"  id={recipe_id}: not found, skipping")
                continue
            if r.photo_path is None:
                print(f"  id={recipe_id} {r.dish_name!r}: already null — skip")
                continue

            if r.source_url and "#" in r.source_url:
                slug = r.source_url.split("#", 1)[1]
            else:
                slug = _slugify(r.dish_name)

            print(f"  id={recipe_id} {r.dish_name!r} card_page={card_page}:", end=" ", flush=True)

            try:
                bbox = await identify_inset_bbox(PDF, card_page)
            except Exception as exc:
                print(f"bbox detection failed: {exc}")
                continue

            old_photo = r.photo_path

            if bbox is None:
                print("no real photo → clearing")
                _delete_photo(old_photo)
                r.photo_path = None
            else:
                print(f"bbox={bbox} → cropping")
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
                except Exception as exc:
                    print(f"photo save failed: {exc}")
                    continue

            db.add(r)
            db.commit()

    print("\n=== Done ===")


asyncio.run(main())
