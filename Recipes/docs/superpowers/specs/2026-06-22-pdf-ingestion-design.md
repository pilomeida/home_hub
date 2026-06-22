# PDF Cookbook Ingestion — Design Spec

**Date:** 2026-06-22
**Status:** Approved

## Overview

Adapt the recipe app to ingest PDF cookbooks via a web UI upload flow. Adds explicit macro fields (protein, fat, carbs, fiber), a multi-value cooking types field, and an interactive 3-at-a-time review flow so extraction quality can be tuned iteratively before committing to a full book import.

## 1. Data Model Changes

### New fields on `Recipe`

| Field | Type | Notes |
|---|---|---|
| `protein_g` | `Optional[int]` | grams of protein per portion |
| `fat_g` | `Optional[int]` | grams of fat per portion |
| `carbs_g` | `Optional[int]` | grams of carbs per portion |
| `fiber_g` | `Optional[int]` | grams of fiber per portion — often null |
| `cooking_types` | `str` | JSON array, default `"[]"` |

### `cooking_types` values (multi-select, all that apply)
`oven`, `cooktop`, `microwave`, `blender`, `no-cook`, `air-fryer`, `other`

A recipe may have multiple: e.g. `["microwave", "air-fryer"]` or `["cooktop", "oven"]`.

Add a `cooking_types_list` property (mirrors `macro_tags_list`).

### `source_url` for PDF recipes
Synthetic unique key: `pdf:<book-slug>#<recipe-title-slug>`
e.g. `pdf:cooking-abs#protein-brownie`

This preserves the unique constraint and naturally deduplicates re-imports.

### Migration
New fields are nullable — add via `ALTER TABLE recipes ADD COLUMN` migration script run at startup if the column doesn't already exist. No data loss to existing records.

### `RecipeCreate` / `RecipeUpdate`
Add the five new fields to both input models.

## 2. PDF Extraction Pipeline (`app/pdf_extractor.py`)

### Dependencies
- `pdfplumber` — text extraction (already common in the ecosystem)
- `pdf2image` + `poppler` — render pages to PNG for vision pass
- `anthropic` — already present

### Per-PDF pipeline

**Step 1 — Text pass (pdfplumber)**
Extract text from every page → `{page_num: str}`. Pages with < 50 meaningful characters are flagged as likely photo/image pages.

**Step 2 — Vision pass (Claude)**
For each flagged photo page, render as PNG and send to Claude vision to extract any overlaid text (e.g. Cooking ABS macro lines: `KCALS 267  P 37.3g  F 9g  C 16.8g`). Merge result back into the page-text map.

**Step 3 — Boundary detection**
Single Claude call with the full page-text map. Returns:
```json
[
  {"recipe_title": "Protein Brownie", "pages": [20, 21]},
  {"recipe_title": "Ferrero Rocher Mug Cake", "pages": [22, 23]},
  ...
]
```
Pages are **not exclusive** — one page can belong to multiple recipes (e.g. a shared sauce page, or a page with two short recipes). Claude is explicitly told this.

**Step 4 — Random sample**
Randomly select 3 recipes from the detected list. Only these 3 proceed to per-recipe extraction. The full recipe list is stored in the session so future "import more" runs can pick from the remainder.

**Step 5 — Per-recipe extraction**
For each sampled recipe: union its pages' text (sorted, deduped), call the updated `extract_recipe()` with the new prompt fields. Returns a structured dict.

### Updated extraction prompt (additions)
```
- cooking_types: array of strings — all that apply from: "oven", "cooktop",
  "microwave", "blender", "no-cook", "air-fryer", "other". Infer from
  instructions text. Can be multiple.
- protein_g: integer or null — grams of protein per portion
- fat_g: integer or null — grams of fat per portion
- carbs_g: integer or null — grams of carbs per portion
- fiber_g: integer or null — grams of fiber per portion
```

Macro values: parse from whatever format the PDF uses:
- `Calories - 558, Protein - 22.8g` (Broccoli Mum)
- `KCALS 267  P 37.3g  F 9g  C 16.8g` (Cooking ABS)

Round floats to nearest integer.

## 3. Session State (`data/import_sessions/<session_id>.json`)

```json
{
  "session_id": "abc123",
  "pdf_filename": "cooking-abs.pdf",
  "book_title": "Cooking ABS",
  "book_slug": "cooking-abs",
  "all_recipes": [
    {"recipe_title": "Protein Brownie", "pages": [20, 21], "status": "pending"},
    ...
  ],
  "sampled_indices": [0, 7, 23],
  "extracted": {
    "0": { <full extracted recipe dict>, "status": "pending" },
    "7": { ... },
    "23": { ... }
  },
  "extraction_complete": true,
  "created_at": "2026-06-22T..."
}
```

Statuses per extracted recipe: `pending` → `approved` / `skipped`. Saved recipes also get a `saved_recipe_id`.

File survives server restart. Session directory created at startup if missing.

## 4. Web UI

### Routes (`app/routes_import.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/import` | Upload form |
| POST | `/import` | Receive PDF, start background pipeline, redirect to progress |
| GET | `/import/{sid}` | Progress page (polls status) |
| GET | `/import/{sid}/status` | JSON status endpoint for polling |
| GET | `/import/{sid}/review` | Review next 3 pending recipes |
| POST | `/import/{sid}/review` | Save/skip decisions, redirect back to review or to summary |
| GET | `/import/{sid}/summary` | Final summary: X saved, Y skipped, links |
| POST | `/import/{sid}/more` | Sample N more pending recipes, extract them, redirect to review |

### Upload page (`GET /import`)
- File input (PDF only)
- "Book title" text field (pre-filled from filename on client side, editable)
- Note: *"We'll start with 3 sample recipes. You can import the rest once happy."*

### Progress page (`GET /import/{sid}`)
- Polls `/import/{sid}/status` every 2 seconds
- Shows: book title, total recipes detected, extraction progress (e.g. "Extracting 2 of 3...")
- Auto-redirects to review page when `extraction_complete: true`

### Review page (`GET /import/{sid}/review`)
- Shows 3 editable recipe cards (or fewer if < 3 remain)
- Each card shows all fields including new macros and cooking types
- Per-card actions: **Save** / **Skip** (radio or button group)
- Inline editing: all text fields editable in place
- One submit button posts all 3 decisions
- Progress indicator: "3 of 47 recipes reviewed" (shows total from book)

### Summary page
- Count of saved vs skipped
- Links to each saved recipe's detail page
- "Import more" button → POSTs to `/import/{sid}/more`, which randomly samples 3 more `pending` recipes from `all_recipes`, extracts them, and redirects back to the review page

## 5. Existing Code Updates

| File | Change |
|---|---|
| `app/models.py` | Add 5 new fields + `cooking_types_list` property + update `RecipeCreate`/`RecipeUpdate` |
| `app/extractor.py` | Update `EXTRACTION_PROMPT` + `_validate_extraction` for new fields |
| `app/routes_add.py` | Add new fields to save/update handlers |
| `app/templates/add.html` | New fields in manual add/edit form |
| `app/templates/detail.html` | Display macros + cooking types |
| `app/templates/browse.html` | Filter by cooking_type (post-query JSON filter, same pattern as macro) |
| `app/templates/base.html` | Add "Import PDF" nav link |
| `app/main.py` | Register import router; run migration at startup |
| `requirements.txt` | Add `pdfplumber`, `pdf2image` |

## 6. Out of Scope
- Photo extraction from PDF pages (no images saved for PDF-sourced recipes, photo_path = null)
- Full-book import in one go (available via "import more" in subsequent sessions once quality confirmed)
- Telegram bot PDF support
