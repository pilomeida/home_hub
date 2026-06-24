# Broccoli Mum PDF Ingestion — Design Spec

**Date:** 2026-06-24
**Status:** Approved
**Supersedes:** Portions of `2026-06-22-pdf-ingestion-design.md` (vision pipeline replaces text pipeline for image-based PDFs)

---

## Overview

Extend the recipe app to ingest **image-based PDF cookbooks** — specifically *The Maximum Weight Loss Recipe Book* by Broccoli Mum (292 pages, 100% image-rendered, pdfplumber yields zero text). The pipeline uses Claude Vision for OCR and extraction, a TOC-guided window strategy for accurate photo-to-recipe association, and an iterative test-run loop (5 recipes at a time) before committing to a full-book bulk import.

---

## 1. Data Model Changes

### New field: `notes`
```python
notes: Optional[str] = None  # intro/observation paragraph from the recipe card
```
Added to `Recipe`, `RecipeCreate`, and `RecipeUpdate`. Displayed on the detail page below the title block, above ingredients. Not filterable on browse.

### Migration
Both `notes` (TEXT) and `source_title` (VARCHAR — already in the model but missing from the live DB) are added to `_NEW_COLUMNS` in `migrate_db()`. Idempotent `ALTER TABLE ADD COLUMN` at startup.

### Badge → `cooking_types` mapping

| Book badge | `cooking_types` value(s) |
|---|---|
| Blender | `blender` |
| Food Processor | `blender` |
| Microwave friendly | `microwave` |
| Oven / Air fryer | `oven`, `air-fryer` |
| Freezer | `no-cook` |
| Waffle maker | `other` |
| Batch Work, Meal Prep | ignored |
| Max. Weight loss, Quick & easy, Worth the effort | ignored |

`macro_tags` (protein-rich, low-carb, etc.) are inferred from ingredient/nutritional content — never from badge icons.

### Macros + servings
- `Calories - N, Protein - Ng, Fat - Ng, Carbs - Ng` footer at bottom of recipe card → `calories_per_portion`, `protein_g`, `fat_g`, `carbs_g` (per serving)
- `Serves ●●` where each dot = 1 serving → `portions` (Claude counts the dots)

---

## 2. Extraction Pipeline

### Auto-detection
On upload, `is_image_pdf()` checks pdfplumber output across the first 10 pages. If < 100 chars total → image-based PDF → vision pipeline. Otherwise → existing text pipeline. Automatic, no UI change.

### Step 1 — TOC extraction
Render pages 3–8 as images, send in a single Claude Vision call.
Returns: `[{"recipe_title": "Brownie Batter Blended Oats", "page": 61}, ...]`

Stored as `toc_recipes` in the session — the ground-truth recipe inventory for the whole book. Re-extracted fresh on each upload (so TOC OCR errors surface and can be fixed iteratively).

### Step 2 — TOC-guided dynamic window
Sort all recipe card pages from the TOC: `[p1, p2, p3, ...]`

For the recipe at card page `pN`:
```
window = [prev_card_page + 1  …  next_card_page - 1]
```
Render **all pages in the window** at 150 dpi. Send them all in one Vision call, telling Claude:
- "Page `pN` is the recipe card for `[recipe_name]`"
- "All other pages in the window are candidates — identify which one is this recipe's food photo"

This handles all real-world sequences correctly:
- `photo → recipe` → window = 2 pages → unambiguous
- `photo → photo → recipe` → window = 3 pages → Claude picks the matching photo
- `recipe → photo → photo → recipe` → window = 4 pages → Claude correctly splits the two photos between recipes
- No photo: Claude returns `photo_page: null`

### Step 3 — Structured extraction (same Vision call as Step 2)
Claude returns a single JSON object per recipe:

```json
{
  "photo_page": 60,
  "photo_is_inset": false,
  "dish_name": "Brownie Batter Blended Oats",
  "distinguishing_feature": null,
  "notes": "If you love brownie batter...",
  "type": "sweet",
  "subtype": "breakfast",
  "macro_tags": ["vegan", "fiber-rich"],
  "cooking_types": ["blender"],
  "calories_per_portion": 558,
  "protein_g": 23,
  "fat_g": 81,
  "carbs_g": 10,
  "fiber_g": null,
  "portions": 1,
  "ingredients": ["1/2 cup chickpeas*", "1 ripe banana", "..."],
  "toppings": ["Strawberries", "Peanut butter powder drizzle"],
  "prep_time_minutes": null,
  "cook_time_minutes": null,
  "instructions": "1. Blend everything...\n2. Serve with...",
  "missing_critical_info": false
}
```

Extraction prompt instructions:
- Count `Serves ●` dots for `portions`
- Map badge icons to `cooking_types` per table above
- Extract `notes` from the intro/observation paragraph (right column, above METHOD)
- Parse macro footer → macro fields (per serving)
- `macro_tags` from ingredient/nutritional content only
- `toppings` merged into the `ingredients` list with a `"TOPPINGS:"` prefix line (e.g. `"TOPPINGS: Strawberries, PB powder drizzle"`); no separate DB field
- `distinguishing_feature` maps to `Recipe.distinguisher`
- Photo identification: full-page food photo → `photo_page`; small photo inset in card → `photo_is_inset: true`; illustration or decorative graphic → neither

### Step 4 — Photo saving
- **Full-page photo** (`photo_page` set): render that page at 150 dpi, save as `app/static/photos/pdf-{book_slug}-{recipe_slug}.jpg`
- **Inset photo** (`photo_is_inset: true`): render the card page itself at 150 dpi and save as-is (speech bubble tips and overlaid text are left intact — no cropping)
- **No food photo**: `photo_path = null`

### New functions (`app/pdf_extractor.py`)

| Function | Signature | Purpose |
|---|---|---|
| `is_image_pdf` | `(pdf_path, check_pages=10) → bool` | auto-detect image-based PDF |
| `extract_toc_vision` | `(pdf_path) → list[dict]` | OCR TOC pages, return title+page list |
| `build_recipe_windows` | `(toc_recipes) → list[dict]` | compute dynamic window for each recipe |
| `extract_recipe_vision` | `(pdf_path, window, book_slug) → dict` | render window pages + single Vision call |
| `create_vision_session` | `(pdf_path, book_title, book_slug, n_sample, test_mode, session_id) → PdfIngestionSession` | full pipeline |

The existing `create_session()` (text pipeline) is untouched.

### `VISION_EXTRACTION_PROMPT`
A module-level constant in `pdf_extractor.py`. Edited directly in code between test runs during iterative fine-tuning. No UI for prompt editing.

---

## 3. Session Management

### New `PdfIngestionSession` fields
| Field | Type | Purpose |
|---|---|---|
| `toc_recipes` | `list[dict]` | full TOC inventory (title + page) |
| `pipeline` | `str` | `"vision"` or `"text"` |
| `test_mode` | `bool` | `True` for 5-recipe test runs |

### Session file
Stored in `data/import_sessions/{sid}.json` as before. Each upload creates a fresh session — no state reused between test runs.

---

## 4. Test Run & Iteration Loop

### Upload form
- **Test mode** checkbox (default: checked). Label: *"Extract 5 random recipes for review"*
- **"Clear test recipes"** button: appears after book title is entered. Posts to `DELETE /import/clear`. Deletes all `recipes` rows where `source_title = book_title AND source_url LIKE 'pdf:<slug>#%'`, and removes matching photos from `app/static/photos/`. Returns `{deleted: N}` as JSON; JS shows a toast.
- "Sample pages" field removed (TOC handles navigation).

### Iteration loop
1. Upload PDF in test mode → extracts 5 random recipes → review page
2. Inspect quality. If extraction is off → **Clear test recipes** → edit `VISION_EXTRACTION_PROMPT` → re-upload
3. Repeat until quality is satisfactory
4. Upload with test mode **off** → full-book run

### `POST /import/clear` endpoint
```
POST /import/clear
body: book_title=Broccoli+Mum&book_slug=broccoli-mum
```
Returns `{"deleted": N}`. HTML forms use POST; the route heading in Section 4 was mistakenly labelled DELETE — routes table in Section 5 is authoritative.

---

## 5. Web UI

### Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/import` | Upload form (updated) |
| POST | `/import` | Receive PDF + test_mode flag, start background pipeline |
| GET | `/import/{sid}` | Progress page |
| GET | `/import/{sid}/status` | JSON polling endpoint |
| GET | `/import/{sid}/review` | 5-card review page (test mode) |
| POST | `/import/{sid}/review` | Submit save/skip decisions |
| GET | `/import/{sid}/bulk` | Scrollable bulk-approve page (full-book mode) |
| POST | `/import/{sid}/bulk` | Save all unchecked recipes |
| GET | `/import/{sid}/summary` | Summary: saved / skipped + clear button |
| POST | `/import/clear` | Delete all recipes by book_title |

### Review page (test mode)
5 editable recipe cards. Per card: photo thumbnail, all fields editable inline, Save/Skip toggle. One Submit button. No pagination — all 5 on one scrollable page.

### Bulk-approve page (full-book mode)
Scrollable list of all extracted recipes. Each row: thumbnail · dish name · subtype · macro summary (cal/P/F/C) · Skip checkbox (default unchecked). Sticky **"Save all unchecked"** button. Count shown: `"N ready, M skipped"`.

### Progress page
- Test mode: `"Extracting 5 sample recipes from Broccoli Mum…"`
- Full-book: `"Extracting recipe N of M — [current recipe name]…"` (session stores current recipe name)

### Summary page
- Saved / skipped counts + links to saved recipe detail pages
- Test mode only: **"Clear these recipes & run again"** button (pre-fills book title in clear endpoint)

### Detail page (`/recipe/{id}`)
New **Notes** block: rendered below title/meta block, above ingredients. Light italicised callout style. Shown only when `recipe.notes` is non-null.

---

## 6. Existing Code Changes

| File | Change |
|---|---|
| `app/models.py` | Add `notes: Optional[str]` to `Recipe`, `RecipeCreate`, `RecipeUpdate` |
| `app/database.py` | Add `notes` + `source_title` to `_NEW_COLUMNS` in `migrate_db()` |
| `app/pdf_extractor.py` | Add `is_image_pdf`, `extract_toc_vision`, `build_recipe_windows`, `extract_recipe_vision`, `create_vision_session`, `VISION_EXTRACTION_PROMPT`; add `toc_recipes`, `pipeline`, `test_mode` to `PdfIngestionSession` |
| `app/routes_import.py` | Add `test_mode` form param; add `DELETE /import/clear` endpoint; add `/{sid}/bulk` routes; update progress status to include current recipe name |
| `app/templates/import.html` | Update upload form (test mode checkbox, clear button); add bulk-approve view |
| `app/templates/detail.html` | Add Notes callout block |
| `app/templates/base.html` | "Import PDF" nav link (already planned) |
| `requirements.txt` | `pdf2image` already present; no new dependencies |

---

## 7. Out of Scope

- Telegram bot PDF support
- Editing `VISION_EXTRACTION_PROMPT` via the web UI
- Per-recipe re-extraction without re-uploading the PDF
- Mixed text+image PDFs in a single book
- marker-pdf / Surya as OCR alternative (viable cost-optimisation path for future)
