"""
Fix round 2: correct three full-page photo assignments, then re-crop all
insets that still show recipe text.

Run on VPS:
    cd /srv/recipe-app
    /srv/recipe-app/venv/bin/python3 app/fix_photos_v2.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, "/srv/recipe-app")
os.chdir("/srv/recipe-app")

from app.pdf_extractor import (
    _save_recipe_photo_vision,
    _slugify,
    identify_inset_bbox,
)
from app.database import engine
from app.models import Recipe
from sqlmodel import Session

PDF = "data/pdf_uploads/8b0bca78.pdf"
BOOK_SLUG = "broccoli-mum"

# ── Full-page photos: user-confirmed correct page numbers ────────────────────
# (card_page used only for slug lookup — photo_page drives the render)
FULL_PAGE_FIXES = [
    (182, "gluten-free-bread", 201),          # Gluten Free Bread
    (190, "sweet-salty-popcorn", 219),         # Sweet & Salty Popcorn
    (228, "ranch-raitha-tzatziki", 287),       # Ranch / Raitha / Tzatziki
]

# ── Insets to re-crop with tighter prompt ────────────────────────────────────
# Mozzarella (225) already correct — excluded.
# Ranch/Raitha/Tzatziki (228) gets full-page photo above — excluded.
SESSION_INSETS = {
    116: 64,   # 5 Minute Blueberry Crumble
    117: 65,   # Mango Sticky Rice
    121: 72,   # Chocolate Lava Cake
    122: 73,   # Cookie Dough Milkshake
    192: 221,  # Cheesy Crispy Beans
}
CONDIMENT_INSETS = {
    218: 276,  # Low Fat Cheese Sauce
    219: 277,  # Oil-Free Hummus
    220: 278,  # Chia Jam
    221: 279,  # Garlic Mayo
    222: 280,  # Gravy
    223: 281,  # Sweet Sweet Dips
    224: 282,  # Smokey BBQ Sauce
    226: 284,  # Spinach Artichoke Dip
    227: 285,  # Queso Dip
    229: 288,  # Asian Peanut Sauce
    230: 289,  # Ketchup
    231: 290,  # Tofu Ricotta
    232: 291,  # Low Fat Peanut Butter
}


def _delete_photo(photo_path: str) -> None:
    p = Path(photo_path)
    if p.exists():
        p.unlink()
        print(f"    deleted {p.name}")


async def main() -> None:
    # ── Phase 1: Save correct full-page photos ───────────────────────────────
    print("\n=== Phase 1: Full-page photo saves ===")
    with Session(engine) as db:
        for recipe_id, slug, photo_page in FULL_PAGE_FIXES:
            r = db.get(Recipe, recipe_id)
            if r is None:
                print(f"  id={recipe_id}: not found, skipping")
                continue
            print(f"  id={recipe_id} {r.dish_name!r}: saving page {photo_page}")
            if r.photo_path:
                _delete_photo(r.photo_path)
            try:
                new_path = await _save_recipe_photo_vision(
                    PDF,
                    photo_page=photo_page,
                    inset_bbox=None,
                    card_page=photo_page,
                    book_slug=BOOK_SLUG,
                    recipe_slug=slug,
                )
                r.photo_path = new_path
                db.add(r)
                db.commit()
                print(f"    saved → {new_path}")
            except Exception as exc:
                print(f"    FAILED: {exc}")

    # ── Phase 2: Re-crop insets with tighter bbox prompt ────────────────────
    print("\n=== Phase 2: Re-crop insets (tighter bbox) ===")
    all_insets = {**SESSION_INSETS, **CONDIMENT_INSETS}

    with Session(engine) as db:
        for recipe_id, card_page in sorted(all_insets.items()):
            r = db.get(Recipe, recipe_id)
            if r is None:
                print(f"  id={recipe_id}: not found, skipping")
                continue

            slug = (r.source_url.split("#", 1)[1]
                    if r.source_url and "#" in r.source_url
                    else _slugify(r.dish_name))

            print(f"  id={recipe_id} {r.dish_name!r} card_page={card_page}:", end=" ", flush=True)

            try:
                bbox = await identify_inset_bbox(PDF, card_page)
            except Exception as exc:
                print(f"bbox detection failed: {exc}")
                continue

            if bbox is None:
                print("no photo found → clearing")
                if r.photo_path:
                    _delete_photo(r.photo_path)
                    r.photo_path = None
                    db.add(r)
                    db.commit()
                continue

            print(f"bbox={bbox} → cropping")
            old_photo = r.photo_path
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
