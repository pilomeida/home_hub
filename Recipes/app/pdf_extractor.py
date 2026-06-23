"""PDF extraction: pdfplumber text pass + programmatic recipe boundary detection."""

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pdfplumber

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

def _render_cover_photo(pdf_path: str, photo_page: int, out_path: Path) -> bool:
    """Render the recipe cover page and mask out text overlays.

    The cover page (card_page - 1) is a full-bleed food photo with the recipe
    title and KCALS/macros overlaid as real PDF text objects.  We locate every
    character's bounding box via pdfplumber, render the page via pdf2image, then
    paste a heavily-blurred crop over each text region — producing a clean photo
    without the overlay text.
    """
    try:
        from pdf2image import convert_from_path
        from PIL import ImageFilter

        # Collect all character bounding boxes from the photo page
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[photo_page - 1]
            page_w = float(page.width)
            page_h = float(page.height)
            chars = page.chars  # each has x0, top, x1, bottom (top-down coords)

        images = convert_from_path(pdf_path, first_page=photo_page, last_page=photo_page, dpi=150)
        if not images:
            return False
        img = images[0]
        img_w, img_h = img.size

        # Scale factors: PDF points → rendered pixels
        sx = img_w / page_w
        sy = img_h / page_h

        # One heavily-blurred version used as paint source for all text regions
        blurred = img.filter(ImageFilter.GaussianBlur(radius=40))

        pad = 6  # pixel padding around each character bbox
        for ch in chars:
            px0 = max(0, int(ch["x0"] * sx) - pad)
            py0 = max(0, int(ch["top"] * sy) - pad)
            px1 = min(img_w, int(ch["x1"] * sx) + pad)
            py1 = min(img_h, int(ch["bottom"] * sy) + pad)
            if px1 > px0 and py1 > py0:
                img.paste(blurred.crop((px0, py0, px1, py1)), (px0, py0))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out_path), "JPEG", quality=85)
        return True
    except Exception as exc:
        print(f"[pdf] cover photo render failed (page {photo_page}): {exc}", flush=True)
        return False


async def _extract_recipe_photo(
    pdf_path: str, card_page: int, book_slug: str, recipe_slug: str
) -> str | None:
    """Async wrapper: renders cover photo (card_page - 1) with text masked out."""
    photo_page = max(1, card_page - 1)
    filename = f"pdf-{book_slug}-{recipe_slug}.jpg"
    out_path = _PHOTOS_DIR / filename
    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(None, _render_cover_photo, pdf_path, photo_page, out_path)
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
