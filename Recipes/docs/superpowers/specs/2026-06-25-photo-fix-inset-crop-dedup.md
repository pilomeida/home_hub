# Photo Fix — Inset Crop & Deduplication Design

## Problem

Three classes of bad photos exist in the Broccoli Mum import:

1. **Duplicate** — 2 pairs of adjacent recipes both claimed the same `photo_page` from Claude.
   The photo file was saved twice (different filenames, same content). One recipe in each pair is
   the rightful owner; the other should have no photo.

2. **Inset-saves-whole-card** — 6 recipes returned `photo_is_inset=True` from the main import
   session. `_save_recipe_photo_vision` saved the entire card page at 150 dpi, including all recipe
   text. The food photo is only a small region of that page.

3. **Condiment false-positive insets** — 15 condiment recipes were extracted by the gap-fill script.
   Claude returned `photo_is_inset=True` for these despite the cards having no real food photograph
   (only decorative elements). The entire recipe card (text and all) was saved as the photo.

## Affected Recipes

### Duplicate pairs (no Vision call needed)

| Rightful owner | Loser (photo → null) |
|---|---|
| Easy 2 Ingredient Dough id=183 (card=202, photo_page=201) | Gluten Free Bread id=182 |
| Oil Free Crisps id=191 (card=220, photo_page=219) | Sweet & Salty Popcorn id=190 |

Resolution rule: the photo page belongs to the recipe card that immediately **follows** it
(photo precedes its card — consistent layout throughout this book).

### Inset recipes (Vision bbox call per recipe)

From main session (session id `596259e2`):

| DB id | Dish | Card page |
|---|---|---|
| 116 | 5 Minute Blueberry Crumble | 64 |
| 117 | Mango Sticky Rice | 65 |
| 121 | Chocolate Lava Cake | 72 |
| 122 | Cookie Dough Milkshake | 73 |
| 191 | Oil Free Crisps | 220 |
| 192 | Cheesy Crispy Beans | 221 |

From gap-fill (condiment subtype, ids 218–232):

| DB id | Dish |
|---|---|
| 218 | Low Fat Cheese Sauce |
| 219 | Oil-Free Hummus |
| 220 | Chia Jam |
| 221 | Garlic Mayo |
| 222 | Gravy |
| 223 | Sweet Sweet Dips |
| 224 | Smokey BBQ Sauce |
| 225 | Mozzarella |
| 226 | Spinach Artichoke Dip |
| 227 | Queso Dip |
| 228 | Ranch / Raitha / Tzatziki |
| 229 | Asian Peanut Sauce |
| 230 | Ketchup |
| 231 | Tofu Ricotta |
| 232 | Low Fat Peanut Butter |

Card pages for condiments: derived at runtime from the PDF TOC (already in DB `source_url` slug →
page lookup not needed; use the existing photo filename to infer the recipe slug, then query the
DB for its `source_url`). The PDF page number for each condiment card must be looked up from the
TOC or inferred from the session/gap-fill data.

**Simpler approach**: for the condiment recipes, the card page can be extracted from the existing
`pdf_extractor.extract_toc_vision` data. The fix script re-reads
`data/import_sessions/596259e2.json` for the first 100 recipes and queries the full TOC
(pages 3–16) for the remaining entries to get each recipe's card page number.

## Solution

### Phase 1 — Deduplication (no API calls)

For each losing recipe:
1. Set `photo_path = null` in DB.
2. Delete the photo file from `app/static/photos/`.

### Phase 2 — Inset bbox detection (1 Vision call per inset recipe)

New function `identify_inset_bbox(pdf_path, card_page) -> Optional[dict]`:

```python
async def identify_inset_bbox(pdf_path: str, card_page: int) -> Optional[dict]:
    """
    Returns {"x": float, "y": float, "w": float, "h": float} (page fractions)
    for the food photo on the card page, or None if no real food photo exists.
    """
```

Prompt:
```
This is a recipe card page from a cookbook.

Does it contain a FOOD PHOTOGRAPH — a real photograph (not a sketch, illustration,
or clipart) of the prepared dish?

Return JSON only, no fences:
{"has_food_photo": true, "bbox": {"x": 0.12, "y": 0.05, "w": 0.45, "h": 0.38}}

If no food photograph, return:
{"has_food_photo": false, "bbox": null}

x and y are the top-left corner; w and h are width and height — all as fractions
of the page (0.0 to 1.0).
```

For each inset recipe:
- Call `identify_inset_bbox`.
- If `has_food_photo = false`: delete existing photo file, set `photo_path = null` in DB.
- If `has_food_photo = true`: render card page at 150 dpi, crop to bbox (using Pillow),
  save as JPEG (quality=85), update `photo_path` in DB.

### Phase 3 — Prompt + code update (prevents recurrence)

**`app/pdf_extractor.py` — VISION_EXTRACTION_PROMPT**

Replace `photo_is_inset` field with `inset_bbox`:

```
- inset_bbox: if the recipe card page ({card_page}) contains a food photograph (a real
  photograph of the prepared dish — NOT a sketch, illustration, clipart, or decorative
  graphic), return {{"x": float, "y": float, "w": float, "h": float}} where x,y is the
  top-left corner and w,h the width/height of the photo region, all as fractions of the
  page (0.0–1.0 range). Return null if there is no food photograph on the card page.
```

Add photo uniqueness rule to PHOTO IDENTIFICATION section:
```
Important: If a full-page photo page falls between two recipe card pages, assign it to
the recipe card that immediately follows it (the photo precedes its recipe). Never assign
the same photo_page value to two different recipes.
```

**`app/pdf_extractor.py` — `_save_recipe_photo_vision`**

Replace `photo_is_inset: bool` parameter with `inset_bbox: Optional[dict]`:

```python
async def _save_recipe_photo_vision(
    pdf_path: str,
    photo_page: Optional[int],
    inset_bbox: Optional[dict],   # replaces photo_is_inset
    card_page: int,
    book_slug: str,
    recipe_slug: str,
) -> Optional[str]:
```

Logic:
- If `photo_page` is not None → render that page, save full image (existing behaviour).
- If `inset_bbox` is not None → render `card_page`, crop to bbox using Pillow:
  ```python
  w_px, h_px = img.size
  x1 = int(bbox["x"] * w_px); y1 = int(bbox["y"] * h_px)
  x2 = int((bbox["x"] + bbox["w"]) * w_px); y2 = int((bbox["y"] + bbox["h"]) * h_px)
  img = img.crop((x1, y1, x2, y2))
  ```
  Save cropped image.
- Otherwise → return None.

Update all callers: `create_vision_session` and the gap-fill script's direct call
(for future gap-fill runs; the existing gap-fill script on VPS does not need patching —
it has already run).

**`app/routes_import.py` — `submit_review`**

Update the call to `_save_recipe_photo_vision` to pass `inset_bbox` instead of
`photo_is_inset`.

## Deliverables

1. `app/fix_photos.py` — one-time targeted fix script, runs on VPS, idempotent.
2. Updated `app/pdf_extractor.py` — `_save_recipe_photo_vision` signature + prompt changes.
3. Updated `app/routes_import.py` — caller updated.

## Constraints

- Fix script runs on VPS via `scp` + `ssh`, uses the venv python and the existing PDF at
  `data/pdf_uploads/8b0bca78.pdf`.
- Only the 23 identified recipes are touched; all others are untouched.
- PDF path, book slug, session JSON path, and photo directory are hardcoded in the fix script
  (it is a one-off, not a generic utility).
- Model for bbox detection: `claude-sonnet-4-6` (same as extraction pipeline).
- No new dependencies; Pillow is already available (used in existing photo pipeline).
- Deploy: `rsync app/ → /srv/recipe-app/app/`, then `chown -R recipe-app:recipe-app`,
  then `systemctl restart recipe-app`. The fix script is run separately over SSH.
