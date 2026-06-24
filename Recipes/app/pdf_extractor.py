"""PDF extraction: pdfplumber text pass + programmatic recipe boundary detection."""

import asyncio
import base64
import io
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pdfplumber
from pdf2image import convert_from_path

_PHOTOS_DIR = Path("app/static/photos")

_SESSIONS_DIR = Path("data/import_sessions")
_MAX_PAGES_PER_RECIPE = 6

# Recipe card pages reliably contain "INGREDIENTS" as a section header
_INGREDIENTS_RE = re.compile(r'\bINGREDIENTS\b')
# Skip these when looking for the recipe title
_SKIP_LINE_RE = re.compile(r'portion|prep:|@|KCALS?|\bCALS?\b', re.IGNORECASE)


# ── Sync helpers ──────────────────────────────────────────────────────────────

@dataclass
class PageText:
    page_num: int
    text: str


def extract_page_texts(pdf_path: str) -> dict[int, PageText]:
    """Extract text from every page of a PDF via pdfplumber."""
    result = {}
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = (page.extract_text() or "").strip()
            result[i + 1] = PageText(page_num=i + 1, text=text)
    return result


def find_recipe_boundaries(page_texts: dict[int, str]) -> list[dict]:
    """Find recipe card pages by locating INGREDIENTS headers.

    Each recipe owns its photo page (p-1, which carries KCALS/macros) plus its
    card page (p) and any trailing directions pages — up to but NOT including
    the next recipe's photo page.  The old allocated_up_to approach was stealing
    photo pages from recipes 2+ and giving them to the preceding recipe.
    """
    sorted_pages = sorted(page_texts.keys())
    ingredient_pages = [
        p for p in sorted_pages
        if _INGREDIENTS_RE.search(page_texts.get(p, ""))
    ]

    recipes = []
    for i, p in enumerate(ingredient_pages):
        start = max(p - 1, 1)  # always include own photo page
        if i + 1 < len(ingredient_pages):
            next_card = ingredient_pages[i + 1]
            # Stop before the next recipe's photo page (next_card - 1 is exclusive)
            end = min(next_card - 1, p + _MAX_PAGES_PER_RECIPE)
        else:
            end = p + _MAX_PAGES_PER_RECIPE
        pages = list(range(start, end))
        title = _title_from_card_page(page_texts.get(p, ""))
        recipes.append({"recipe_title": title, "pages": pages, "card_page": p})

    return recipes


def _title_from_card_page(text: str) -> str:
    """Extract recipe title: all non-skip lines before INGREDIENTS on the card page.

    Recipe titles sometimes span two lines (e.g. "Chocolate Mug Cake" / "with Tofu").
    Joining all valid lines produces the full title. Short ALL_CAPS fragments
    (book header/footer like "COOKING ABS") are filtered out.
    """
    parts = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if _INGREDIENTS_RE.search(line):
            break
        if _SKIP_LINE_RE.search(line) or re.match(r"^\d+$", line):
            continue
        # Drop short all-caps fragments (book header/footer artefacts)
        if line.isupper() and len(line.split()) <= 3:
            continue
        parts.append(line)
    return " ".join(parts) if parts else "Unknown Recipe"


def assemble_recipe_text(page_texts: dict[int, str], pages: list[int]) -> str:
    """Concatenate page texts in sorted order, deduped."""
    parts = []
    for p in sorted(set(pages)):
        if page_texts.get(p):
            parts.append(f"[Page {p}]\n{page_texts[p]}")
    return "\n\n".join(parts)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _empty_recipe_data() -> dict:
    return {
        "dish_name": None, "distinguishing_feature": None, "type": "savory",
        "subtype": None, "macro_tags": [], "calories_per_portion": None,
        "ingredients": [], "prep_time_minutes": None, "cook_time_minutes": None,
        "portions": None, "instructions": None, "missing_critical_info": True,
        "cooking_types": [], "protein_g": None, "fat_g": None,
        "carbs_g": None, "fiber_g": None,
    }


# ── Anthropic client ──────────────────────────────────────────────────────────

_anthropic_client = None


def _get_client():
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import AsyncAnthropic
        from app.config import settings
        _anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client


def is_image_pdf(pdf_path: str, check_pages: int = 10) -> bool:
    """True if pdfplumber extracts < 100 chars across the first check_pages pages."""
    total = 0
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:check_pages]:
            total += len((page.extract_text() or "").strip())
            if total >= 100:
                return False
    return True


def build_recipe_windows(toc_recipes: list[dict]) -> list[dict]:
    """Compute the dynamic page window for each recipe.

    Window spans from (prev_card + 1) to (next_card - 1), clamped to ±3
    pages from the card page. The card page is always included.
    """
    card_pages = [r["page"] for r in toc_recipes]
    result = []
    for i, recipe in enumerate(toc_recipes):
        p = recipe["page"]
        prev_end = card_pages[i - 1] if i > 0 else max(1, p - 4)
        next_start = card_pages[i + 1] if i < len(card_pages) - 1 else p + 4
        start = max(prev_end + 1, p - 3)
        end = min(next_start - 1, p + 3)
        result.append({
            "recipe_title": recipe["recipe_title"],
            "card_page": p,
            "window_pages": list(range(start, end + 1)),
        })
    return result


# ── Session state ─────────────────────────────────────────────────────────────

@dataclass
class PdfIngestionSession:
    session_id: str
    pdf_path: str
    book_title: str
    book_slug: str
    all_recipes: list[dict]
    sampled_indices: list[int]
    extracted: dict          # str(index) -> recipe dict with "status" key
    extraction_complete: bool
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    error: Optional[str] = None
    total_pages: int = 0
    used_windows: list = field(default_factory=list)  # kept for session compat
    sample_pages: list = field(default_factory=list)  # user-specified page numbers
    auto_approved: bool = False  # True when recipes have been saved without review
    toc_recipes: list = field(default_factory=list)
    pipeline: str = "text"
    test_mode: bool = False
    current_recipe: str = ""


def _session_path(session_id: str) -> Path:
    return _SESSIONS_DIR / f"{session_id}.json"


def save_session(session: PdfIngestionSession) -> None:
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _session_path(session.session_id).write_text(
        json.dumps(session.__dict__, indent=2)
    )


def load_session(session_id: str) -> PdfIngestionSession:
    data = json.loads(_session_path(session_id).read_text())
    return PdfIngestionSession(**data)


def get_pending_batch(
    session: PdfIngestionSession, n: int = 3
) -> list[tuple[int, dict]]:
    """Return up to n (index, recipe_dict) pairs with status == 'pending'."""
    result = []
    for idx in session.sampled_indices:
        if len(result) >= n:
            break
        recipe = session.extracted.get(str(idx))
        if recipe and recipe.get("status") == "pending":
            result.append((idx, recipe))
    return result


# ── Photo extraction ─────────────────────────────────────────────────────────

def _render_recipe_photo(pdf_path: str, card_page: int, out_path: Path) -> bool:
    """Crop the top-left quadrant of the recipe card page (the food photo).

    The recipe card page layout has the food photo clean in the top-left quadrant
    (x: 0–50%, y: 0–50%) with the ingredients/directions text on the right and bottom.
    """
    try:
        from pdf2image import convert_from_path
        images = convert_from_path(pdf_path, first_page=card_page, last_page=card_page, dpi=150)
        if not images:
            return False
        img = images[0]
        w, h = img.size
        photo = img.crop((0, 0, w // 2, h // 2))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        photo.save(str(out_path), "JPEG", quality=85)
        return True
    except Exception as exc:
        print(f"[pdf] photo render failed (page {card_page}): {exc}", flush=True)
        return False


async def _extract_recipe_photo(
    pdf_path: str, card_page: int, book_slug: str, recipe_slug: str
) -> str | None:
    """Async wrapper: crops the food photo from the top-left quadrant of the card page."""
    filename = f"pdf-{book_slug}-{recipe_slug}.jpg"
    out_path = _PHOTOS_DIR / filename
    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(None, _render_recipe_photo, pdf_path, card_page, out_path)
    return str(out_path) if ok else None


# ── Full pipeline ─────────────────────────────────────────────────────────────

async def create_session(
    pdf_path: str,
    book_title: str,
    book_slug: str,
    session_id: str | None = None,
    sample_pages: list[int] | None = None,
) -> PdfIngestionSession:
    """Extract all recipes from a PDF using pdfplumber + programmatic boundary detection.

    Text-only pipeline — no vision pass needed for cookbooks where content is
    rendered as PDF text objects (not baked into images).
    """
    sid = session_id or str(uuid.uuid4())[:8]
    print(f"[pdf] session {sid}: {book_title!r}", flush=True)

    # Step 1: text pass on all pages
    print(f"[pdf] text pass: {pdf_path}", flush=True)
    page_texts_raw = extract_page_texts(pdf_path)
    full_texts = {p: pt.text for p, pt in page_texts_raw.items()}
    total_pages = len(full_texts)
    print(f"[pdf] {total_pages} pages", flush=True)

    # Step 2: find recipe boundaries, then optionally filter to sample pages
    recipe_metas = find_recipe_boundaries(full_texts)
    if sample_pages:
        sample_set = set(sample_pages)
        recipe_metas = [
            m for m in recipe_metas
            if m["card_page"] in sample_set or m["card_page"] - 1 in sample_set
        ]
        print(f"[pdf] filtered to {len(recipe_metas)} sample recipes (pages {sample_pages})", flush=True)
    all_recipes = [{**m, "status": "pending"} for m in recipe_metas]
    print(f"[pdf] {len(all_recipes)} recipes detected", flush=True)

    # Save immediately so the progress page can show the total count
    session = PdfIngestionSession(
        session_id=sid,
        pdf_path=pdf_path,
        book_title=book_title,
        book_slug=book_slug,
        all_recipes=all_recipes,
        sampled_indices=list(range(len(all_recipes))),
        extracted={},
        extraction_complete=False,
        total_pages=total_pages,
        sample_pages=sample_pages or [],
    )
    save_session(session)

    # Step 3: extract each recipe + photo concurrently, saving progress after each
    from app.extractor import extract_recipe
    error: Optional[str] = None
    try:
        for idx, meta in enumerate(recipe_metas):
            title = meta["recipe_title"]
            recipe_slug = _slugify(title)
            print(f"[pdf] extracting {idx + 1}/{len(recipe_metas)}: {title!r}", flush=True)
            text = assemble_recipe_text(full_texts, meta["pages"])
            source_url = f"pdf:{book_slug}#{recipe_slug}"

            # Run Claude extraction and photo render concurrently
            card_page = meta.get("card_page")
            extract_coro = extract_recipe(text, source_url)
            photo_coro = _extract_recipe_photo(pdf_path, card_page, book_slug, recipe_slug) if card_page else None

            if photo_coro:
                results = await asyncio.gather(extract_coro, photo_coro, return_exceptions=True)
                data = results[0] if not isinstance(results[0], Exception) else _empty_recipe_data()
                photo_path = results[1] if not isinstance(results[1], Exception) else None
                if isinstance(results[0], Exception):
                    print(f"[pdf] extraction failed for {title!r}: {results[0]}", flush=True)
            else:
                try:
                    data = await extract_coro
                except Exception as exc:
                    data = _empty_recipe_data()
                    print(f"[pdf] extraction failed for {title!r}: {exc}", flush=True)
                photo_path = None

            data.update({
                "status": "pending",
                "source_url": source_url,
                "book_title": book_title,
                "photo_path": photo_path,
            })
            session.extracted[str(idx)] = data
            save_session(session)
    except Exception as exc:
        error = str(exc)
        print(f"[pdf] session {sid} error: {exc}", flush=True)

    session.extraction_complete = True
    session.error = error
    save_session(session)
    print(f"[pdf] session {sid} complete: {len(session.extracted)} extracted", flush=True)
    return session


# ── Vision extraction ─────────────────────────────────────────────────────────

VISION_EXTRACTION_PROMPT = """\
You are analyzing pages from the cookbook "Broccoli Mum — The Maximum Weight Loss Recipe Book".

You are given {n_pages} page image(s). Page {card_page} is the recipe card for "{recipe_title}".
The other pages (if any) are adjacent pages that may contain a food photo for this recipe.

Pages provided: {page_labels}

=== RECIPE CARD EXTRACTION (from page {card_page}) ===

Extract EXACTLY these fields as a JSON object:

- dish_name: The recipe title as printed in the dark green title box (top-left of card)
- distinguishing_feature: What makes this version unique in ≤8 words, or null
- notes: The intro/observation paragraph in the RIGHT column of the recipe card, above the METHOD section. Copy it in full. This is personal author text about the recipe. Return null if absent.
- type: "sweet" or "savory"
- subtype: one of "main", "dessert", "snack", "soup", "salad", "breakfast", "side", "drink", or null
- macro_tags: array from ["protein-rich","low-carb","keto","vegan","gluten-free","fiber-rich","high-fat","dairy-free"]. Infer ONLY from ingredients and nutrition — NEVER from badge icons.
- cooking_types: array built from badge icons using ONLY these mappings:
    Blender icon OR Food Processor icon → "blender"
    Microwave icon → "microwave"
    Oven/Air fryer icon → both "oven" AND "air-fryer"
    Waffle maker icon → "other"
    Freezer icon → "no-cook"
    Ignore entirely: Batch Work, Meal Prep, Max Weight Loss, Quick & Easy, Worth the Effort
- calories_per_portion: integer from footer line "Calories - N" (per serving). Null if absent.
- protein_g: integer from footer "Protein - Ng" — round to nearest integer. Null if absent.
- fat_g: integer from footer "Fat - Ng" — round to nearest integer. Null if absent.
- carbs_g: integer from footer "Carbs - Ng" — round to nearest integer. Null if absent.
- fiber_g: integer if stated, else null
- portions: count the filled dots (●) after the word "Serves" on the card — each dot = 1 serving
- ingredients: array of strings — all items under INGREDIENTS with quantities exactly as written. If a TOPPINGS section exists, append one final item: "TOPPINGS: item1, item2, ..."
- prep_time_minutes: integer or null
- cook_time_minutes: integer or null
- instructions: full METHOD steps as a markdown numbered list. Null if absent.
- missing_critical_info: true ONLY if dish_name AND ingredients AND instructions are all absent

=== PHOTO IDENTIFICATION ===

- photo_page: the page NUMBER (an integer from the list above) that shows a full-page food photograph of this recipe's finished dish. Return null if no other page in the set is a food photo for this recipe.
- photo_is_inset: true if the recipe card page ({card_page}) itself contains a small food photograph inset (not a decorative illustration or clipart graphic), false otherwise.

Return ONLY a valid JSON object. No commentary, no markdown fences.\
"""


async def extract_toc_vision(
    pdf_path: str,
    toc_page_range: tuple[int, int] = (3, 8),
) -> list[dict]:
    """OCR the TOC pages via Claude Vision. Returns [{recipe_title, page}]."""
    start, end = toc_page_range
    loop = asyncio.get_running_loop()
    images = await loop.run_in_executor(
        None,
        lambda: convert_from_path(pdf_path, first_page=start, last_page=end, dpi=120),
    )

    content = []
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.standard_b64encode(buf.getvalue()).decode()
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })
    content.append({
        "type": "text",
        "text": (
            "These pages are from a cookbook's table of contents. "
            "Extract every recipe entry as a JSON array: "
            '[{"recipe_title": "...", "page": N}, ...]. '
            "Include only actual recipe entries — not chapter headings, "
            "section titles, or page numbers without a recipe name. "
            "Return ONLY valid JSON, no commentary, no markdown fences."
        ),
    })

    client = _get_client()
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        temperature=0,
        messages=[{"role": "user", "content": content}],
    )
    text = message.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


async def extract_recipe_vision(
    pdf_path: str,
    window_pages: list[int],
    card_page: int,
    recipe_title: str,
    book_slug: str,
) -> dict:
    """Render the window pages and call Claude Vision for extraction + photo ID."""
    min_p, max_p = min(window_pages), max(window_pages)
    loop = asyncio.get_running_loop()
    all_images = await loop.run_in_executor(
        None,
        lambda: convert_from_path(pdf_path, first_page=min_p, last_page=max_p, dpi=150),
    )

    # Map rendered images to their page numbers
    page_images: dict[int, object] = {}
    for offset, img in enumerate(all_images):
        page_num = min_p + offset
        if page_num in window_pages:
            page_images[page_num] = img

    content = []
    page_labels = []
    for page_num in sorted(page_images):
        buf = io.BytesIO()
        page_images[page_num].save(buf, format="PNG")
        b64 = base64.standard_b64encode(buf.getvalue()).decode()
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })
        label = f"Page {page_num}" + (" [RECIPE CARD]" if page_num == card_page else "")
        page_labels.append(label)

    prompt = VISION_EXTRACTION_PROMPT.format(
        n_pages=len(page_images),
        card_page=card_page,
        recipe_title=recipe_title,
        page_labels=" | ".join(page_labels),
    )
    content.append({"type": "text", "text": prompt})

    client = _get_client()
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        temperature=0,
        messages=[{"role": "user", "content": content}],
    )
    text = message.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


async def _save_recipe_photo_vision(
    pdf_path: str,
    photo_page: Optional[int],
    photo_is_inset: bool,
    card_page: int,
    book_slug: str,
    recipe_slug: str,
) -> Optional[str]:
    """Render and save the recipe photo. Returns saved path or None."""
    target_page = photo_page if photo_page is not None else (card_page if photo_is_inset else None)
    if target_page is None:
        return None
    loop = asyncio.get_running_loop()
    images = await loop.run_in_executor(
        None,
        lambda: convert_from_path(pdf_path, first_page=target_page, last_page=target_page, dpi=150),
    )
    if not images:
        return None
    filename = f"pdf-{book_slug}-{recipe_slug}.jpg"
    out_path = _PHOTOS_DIR / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(str(out_path), "JPEG", quality=85)
    return str(out_path)
