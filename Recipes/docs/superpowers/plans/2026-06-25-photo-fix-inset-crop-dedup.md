# Photo Fix — Inset Crop & Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 23 broken recipe photos in the Broccoli Mum book: remove duplicate photo claims, crop inset photos instead of saving the whole card, and prevent both issues from recurring.

**Architecture:** Three code changes plus one ops step. First update `_save_recipe_photo_vision` to accept an `inset_bbox` dict instead of a boolean flag, update the extraction prompt to return `inset_bbox`, and add `identify_inset_bbox` for targeted bbox detection. Then write and run a one-time fix script that patches the 23 affected recipes in the live DB without re-importing anything.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, SQLite, Pillow (PIL), anthropic SDK, pdf2image/poppler, pytest-asyncio.

## Global Constraints

- Model for all Vision calls: `claude-sonnet-4-6`
- Photo quality: JPEG quality=85
- Photo render DPI: 150
- Photos directory: `app/static/photos/` (relative to repo root / VPS working dir)
- VPS: `root@167.233.51.113`, SSH key `~/.ssh/github_deploy`, app at `/srv/recipe-app/`
- VPS venv python: `/srv/recipe-app/venv/bin/python3`
- VPS PDF path: `data/pdf_uploads/8b0bca78.pdf`
- Book slug: `broccoli-mum`
- Deploy: `rsync app/ root@host:/srv/recipe-app/app/` → `chown -R recipe-app:recipe-app /srv/recipe-app/app/` → `systemctl restart recipe-app`
- Only 23 identified recipes are touched; all others are untouched
- No new dependencies — Pillow and anthropic SDK are already present

---

## File Map

| File | Change |
|---|---|
| `app/pdf_extractor.py` | Replace `photo_is_inset: bool` → `inset_bbox: Optional[dict]` in `_save_recipe_photo_vision`; add Pillow crop; update `VISION_EXTRACTION_PROMPT`; update `create_vision_session` caller; add `INSET_BBOX_PROMPT` constant; add `identify_inset_bbox` function |
| `tests/test_pdf_extractor.py` | Update 5 tests that reference `photo_is_inset`; add 3 new tests for `identify_inset_bbox` and bbox crop |
| `app/fix_photos.py` | New one-off script: dedup + inset repair; runs on VPS |

---

## Task 1: Update `_save_recipe_photo_vision` signature + VISION_EXTRACTION_PROMPT

**Files:**
- Modify: `app/pdf_extractor.py` (lines 363–410, 542–565, 636–646)
- Modify: `tests/test_pdf_extractor.py` (lines 246–341, 354–413)

**Interfaces:**
- Produces: `_save_recipe_photo_vision(pdf_path, photo_page, inset_bbox, card_page, book_slug, recipe_slug) -> Optional[str]` — callers pass `inset_bbox: Optional[dict]` instead of `photo_is_inset: bool`

- [ ] **Step 1: Write the failing tests first**

In `tests/test_pdf_extractor.py`, update the three existing `_save_recipe_photo_vision` tests and two `create_vision_session` tests that reference `photo_is_inset`.

Replace the inset test (line 319–330) with:
```python
@pytest.mark.asyncio
async def test_save_recipe_photo_vision_inset_crops_to_bbox(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_PHOTOS_DIR", tmp_path)
    fake_img = Image.new("RGB", (680, 880), color=(100, 150, 50))
    bbox = {"x": 0.1, "y": 0.05, "w": 0.45, "h": 0.4}
    with patch("app.pdf_extractor.convert_from_path", return_value=[fake_img]):
        result = await _save_recipe_photo_vision(
            "/fake/book.pdf",
            photo_page=None, inset_bbox=bbox,
            card_page=61, book_slug="broccoli-mum", recipe_slug="brownie-batter",
        )
    assert result is not None
    saved = Image.open(tmp_path / "pdf-broccoli-mum-brownie-batter.jpg")
    # Crop box: x1=68,y1=44,x2=374,y2=396 → size (306, 352)
    assert saved.size == (306, 352)
```

Replace the no-photo test (line 333–340) with:
```python
@pytest.mark.asyncio
async def test_save_recipe_photo_vision_no_photo():
    result = await _save_recipe_photo_vision(
        "/fake/book.pdf",
        photo_page=None, inset_bbox=None,
        card_page=61, book_slug="broccoli-mum", recipe_slug="brownie-batter",
    )
    assert result is None
```

The full-page test (line 306–316) needs its keyword arg renamed:
```python
@pytest.mark.asyncio
async def test_save_recipe_photo_vision_full_page(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_PHOTOS_DIR", tmp_path)
    fake_img = Image.new("RGB", (680, 880), color=(100, 150, 50))
    with patch("app.pdf_extractor.convert_from_path", return_value=[fake_img]):
        result = await _save_recipe_photo_vision(
            "/fake/book.pdf",
            photo_page=60, inset_bbox=None,
            card_page=61, book_slug="broccoli-mum", recipe_slug="brownie-batter",
        )
    assert result is not None
    assert (tmp_path / "pdf-broccoli-mum-brownie-batter.jpg").exists()
```

Also update `fake_extraction` in the two `create_vision_session` tests
(`test_create_vision_session_test_mode` line 352 and `test_create_vision_session_full_book_mode` line 392):
change `"photo_is_inset": False` → `"inset_bbox": None`

And update the `extract_recipe_vision` test mock data (line 247 and line 283):
change `"photo_is_inset": False` → `"inset_bbox": None`

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/pedro/Desktop/Claude_Corner/Personal/Recipes
python -m pytest tests/test_pdf_extractor.py -x -q 2>&1 | tail -20
```

Expected: multiple failures referencing `photo_is_inset` / unexpected keyword argument.

- [ ] **Step 3: Update `_save_recipe_photo_vision` in `app/pdf_extractor.py`**

Replace the function at line 542 with:

```python
async def _save_recipe_photo_vision(
    pdf_path: str,
    photo_page: Optional[int],
    inset_bbox: Optional[dict],
    card_page: int,
    book_slug: str,
    recipe_slug: str,
) -> Optional[str]:
    """Render and save the recipe photo. Returns saved path or None."""
    if photo_page is not None:
        target_page = photo_page
        crop = None
    elif inset_bbox is not None:
        target_page = card_page
        crop = inset_bbox
    else:
        return None

    loop = asyncio.get_running_loop()
    images = await loop.run_in_executor(
        None,
        lambda: convert_from_path(pdf_path, first_page=target_page, last_page=target_page, dpi=150),
    )
    if not images:
        return None

    img = images[0]
    if crop is not None:
        w_px, h_px = img.size
        x1 = int(crop["x"] * w_px)
        y1 = int(crop["y"] * h_px)
        x2 = int((crop["x"] + crop["w"]) * w_px)
        y2 = int((crop["y"] + crop["h"]) * h_px)
        img = img.crop((x1, y1, x2, y2))

    filename = f"pdf-{book_slug}-{recipe_slug}.jpg"
    out_path = _PHOTOS_DIR / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "JPEG", quality=85)
    return str(out_path)
```

- [ ] **Step 4: Update `VISION_EXTRACTION_PROMPT` in `app/pdf_extractor.py`**

Replace the two lines of the `=== PHOTO IDENTIFICATION ===` section (lines 404–408):

```
=== PHOTO IDENTIFICATION ===

- photo_page: the page NUMBER (an integer from the list above) that shows a full-page food photograph of this recipe's finished dish. Return null if no other page in the set is a food photo for this recipe. Important: if a full-page photo falls between two recipe card pages, assign it to the card that immediately follows it — never assign the same photo_page to two different recipes.
- inset_bbox: if the recipe card page ({card_page}) contains a food photograph (a real photograph of the prepared dish — NOT a sketch, illustration, clipart, or decorative graphic), return {{"x": float, "y": float, "w": float, "h": float}} where x,y is the top-left corner and w,h the width/height of the photo region, all as fractions of the page (0.0–1.0 range). Return null if there is no food photograph on the card page.

Return ONLY a valid JSON object. No commentary, no markdown fences.
```

(Remove the old `photo_is_inset` line entirely.)

- [ ] **Step 5: Update `create_vision_session` caller in `app/pdf_extractor.py`**

At line 641, change:
```python
raw.get("photo_is_inset", False),
```
to:
```python
raw.get("inset_bbox"),
```

- [ ] **Step 6: Run tests to confirm they pass**

```bash
python -m pytest tests/test_pdf_extractor.py -x -q 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/pdf_extractor.py tests/test_pdf_extractor.py
git commit -m "feat: replace photo_is_inset bool with inset_bbox crop in _save_recipe_photo_vision

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 2: Add `identify_inset_bbox` function

**Files:**
- Modify: `app/pdf_extractor.py` (add after `_save_recipe_photo_vision`, before `create_vision_session`)
- Modify: `tests/test_pdf_extractor.py` (add 2 new tests, update import list)

**Interfaces:**
- Consumes: `_save_recipe_photo_vision` from Task 1 (updated signature)
- Produces: `identify_inset_bbox(pdf_path: str, card_page: int) -> Optional[dict]` — returns `{"x", "y", "w", "h"}` or `None`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_pdf_extractor.py` import list:
```python
from app.pdf_extractor import (
    ...
    identify_inset_bbox,
    INSET_BBOX_PROMPT,
)
```

Add these two tests at the end of the file:

```python
@pytest.mark.asyncio
async def test_identify_inset_bbox_returns_bbox_when_photo_found():
    result_json = '{"has_food_photo": true, "bbox": {"x": 0.1, "y": 0.05, "w": 0.45, "h": 0.4}}'
    fake_img = Image.new("RGB", (680, 880))
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=result_json)])
    )
    with patch("app.pdf_extractor.convert_from_path", return_value=[fake_img]), \
         patch("app.pdf_extractor._get_client", return_value=mock_client):
        result = await identify_inset_bbox("/fake/book.pdf", card_page=73)
    assert result == {"x": 0.1, "y": 0.05, "w": 0.45, "h": 0.4}


@pytest.mark.asyncio
async def test_identify_inset_bbox_returns_none_when_no_photo():
    result_json = '{"has_food_photo": false, "bbox": null}'
    fake_img = Image.new("RGB", (680, 880))
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=result_json)])
    )
    with patch("app.pdf_extractor.convert_from_path", return_value=[fake_img]), \
         patch("app.pdf_extractor._get_client", return_value=mock_client):
        result = await identify_inset_bbox("/fake/book.pdf", card_page=73)
    assert result is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_pdf_extractor.py -k "inset_bbox" -x -q 2>&1 | tail -10
```

Expected: `ImportError` — `identify_inset_bbox` not found yet.

- [ ] **Step 3: Add `INSET_BBOX_PROMPT` and `identify_inset_bbox` to `app/pdf_extractor.py`**

Insert after the closing of `_save_recipe_photo_vision` (after line 565, before `create_vision_session`):

```python
INSET_BBOX_PROMPT = """\
This is a recipe card page from a cookbook.

Does it contain a FOOD PHOTOGRAPH — a real photograph (not a sketch, illustration,
or clipart) of the prepared dish?

Return JSON only, no fences:
{"has_food_photo": true, "bbox": {"x": 0.12, "y": 0.05, "w": 0.45, "h": 0.38}}

If no food photograph, return:
{"has_food_photo": false, "bbox": null}

x and y are the top-left corner; w and h are width and height — all as fractions
of the page (0.0 to 1.0).\
"""


async def identify_inset_bbox(pdf_path: str, card_page: int) -> Optional[dict]:
    """Detect food photo inset on a recipe card page via Vision.

    Returns {"x", "y", "w", "h"} as page fractions, or None if no real food photo.
    """
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
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        },
        {"type": "text", "text": INSET_BBOX_PROMPT},
    ]
    client = _get_client()
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        temperature=0,
        messages=[{"role": "user", "content": content}],
    )
    text = message.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    result = json.loads(text)
    return result.get("bbox") if result.get("has_food_photo") else None
```

- [ ] **Step 4: Run all pdf_extractor tests**

```bash
python -m pytest tests/test_pdf_extractor.py -x -q 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/pdf_extractor.py tests/test_pdf_extractor.py
git commit -m "feat: add identify_inset_bbox for targeted card photo detection

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 3: Write `app/fix_photos.py` fix script

**Files:**
- Create: `app/fix_photos.py`

**Interfaces:**
- Consumes: `identify_inset_bbox` from Task 2, `_save_recipe_photo_vision` from Task 1
- Produces: updated `photo_path` values in the VPS SQLite DB; correct photo files in `app/static/photos/`

**Background — which recipes are affected:**

*Phase 1 — deduplication (no Vision calls):*
- id=182 Gluten Free Bread: wrongly claimed `photo_page=201`; rightful owner is id=183 Easy 2 Ingredient Dough (card=202, photo precedes card). Set photo_path=null, delete file.
- id=190 Sweet & Salty Popcorn: wrongly claimed `photo_page=219`; rightful owner is id=191 Oil Free Crisps (card=220). Set photo_path=null, delete file.

*Phase 2 — inset repair (1 Vision call per recipe):*
- ids 116, 117, 121, 122, 192 — 5 session inset recipes: their photos are full card pages. card_pages: 116→64, 117→65, 121→72, 122→73, 192→221.
- ids 218–232 — 15 condiment recipes: all show card text as photo. Card pages looked up from TOC.
- Note: id=191 (Oil Free Crisps) is NOT in Phase 2 — its `photo_page=219` was used (a correct full-page photo, not the card page).

- [ ] **Step 1: Write the fix script**

Create `app/fix_photos.py`:

```python
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
```

- [ ] **Step 2: Verify the script is syntactically correct locally**

```bash
python3 -c "import ast; ast.parse(open('app/fix_photos.py').read()); print('syntax OK')"
```

Expected output: `syntax OK`

- [ ] **Step 3: Commit**

```bash
git add app/fix_photos.py
git commit -m "feat: add one-time fix_photos script for inset crop + dedup repair

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Deploy app changes and run fix script on VPS

**Files:** No code changes — ops only.

- [ ] **Step 1: Run the full test suite locally**

```bash
python -m pytest tests/test_pdf_extractor.py -q 2>&1 | tail -5
```

Expected: all tests pass (0 failures).

- [ ] **Step 2: rsync updated app code to VPS**

```bash
rsync -avz -e "ssh -i ~/.ssh/github_deploy -o IdentitiesOnly=yes" \
  app/ root@167.233.51.113:/srv/recipe-app/app/
```

- [ ] **Step 3: Fix ownership and restart the service**

```bash
ssh -i ~/.ssh/github_deploy -o IdentitiesOnly=yes root@167.233.51.113 \
  "chown -R recipe-app:recipe-app /srv/recipe-app/app/ && systemctl restart recipe-app && systemctl is-active recipe-app"
```

Expected output: `active`

- [ ] **Step 4: Run the fix script on VPS**

```bash
ssh -i ~/.ssh/github_deploy -o IdentitiesOnly=yes root@167.233.51.113 \
  "cd /srv/recipe-app && /srv/recipe-app/venv/bin/python3 app/fix_photos.py"
```

Watch the output. Each recipe should print one of:
- `already null — skip` (already fixed / idempotent)
- `no real photo → clearing` + `deleted <filename>` (condiment with no inset)
- `bbox={...} → cropping` (recipe with real inset, re-saved cropped)
- `clearing photo` + `deleted <filename>` (dedup loser)

- [ ] **Step 5: Verify in the browser**

Open `http://167.233.51.113:8080/?subtype=condiment` in a browser.

Expected: all condiment recipe cards either show a proper food photo (cropped inset) or show the `📷` placeholder — none should show recipe card text.

Open `http://167.233.51.113:8080/?subtype=breakfast` and look at ids 116, 117, 121, 122.

Expected: Cookie Dough Milkshake, 5 Minute Blueberry Crumble, Mango Sticky Rice, Chocolate Lava Cake show either a cropped food photo or the `📷` placeholder — not full card text.

Check Gluten Free Bread and Sweet & Salty Popcorn: both should now show the `📷` placeholder.
