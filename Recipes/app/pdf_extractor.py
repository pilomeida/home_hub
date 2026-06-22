"""PDF extraction: pdfplumber text pass + programmatic recipe boundary detection."""

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pdfplumber

_MIN_TEXT_CHARS = 50
_SESSIONS_DIR = Path("data/import_sessions")
_MAX_PAGES_PER_RECIPE = 6

# Pages containing this pattern are recipe title/photo pages with macro overlays
_RECIPE_MACRO_RE = re.compile(r'\bKCALS?\s+\d', re.IGNORECASE)


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
    """Detect recipe pages by finding macro lines (KCALS X P Y F Z C W).

    Each recipe spans from its title/macro page to just before the next one,
    capped at _MAX_PAGES_PER_RECIPE.
    """
    sorted_pages = sorted(page_texts.keys())
    starts = [
        p for p in sorted_pages
        if _RECIPE_MACRO_RE.search(page_texts.get(p, ""))
    ]

    recipes = []
    for i, start in enumerate(starts):
        next_start = starts[i + 1] if i + 1 < len(starts) else start + _MAX_PAGES_PER_RECIPE
        pages = list(range(start, min(next_start, start + _MAX_PAGES_PER_RECIPE)))
        title = _title_from_page(page_texts.get(start, ""))
        recipes.append({"recipe_title": title, "pages": pages})

    return recipes


def _title_from_page(text: str) -> str:
    """Extract recipe title: the lines on the macro page that precede the KCALS line."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    title_lines = []
    for line in lines:
        if _RECIPE_MACRO_RE.search(line):
            break
        title_lines.append(line)
    return " ".join(title_lines) if title_lines else "Unknown Recipe"


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


# ── Full pipeline ─────────────────────────────────────────────────────────────

async def create_session(
    pdf_path: str,
    book_title: str,
    book_slug: str,
    session_id: str | None = None,
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

    # Step 2: find recipe boundaries from KCALS pattern
    recipe_metas = find_recipe_boundaries(full_texts)
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
    )
    save_session(session)

    # Step 3: extract each recipe individually with progress persistence
    from app.extractor import extract_recipe
    error: Optional[str] = None
    try:
        for idx, meta in enumerate(recipe_metas):
            title = meta["recipe_title"]
            print(f"[pdf] extracting {idx + 1}/{len(recipe_metas)}: {title!r}", flush=True)
            text = assemble_recipe_text(full_texts, meta["pages"])
            source_url = f"pdf:{book_slug}#{_slugify(title)}"
            try:
                data = await extract_recipe(text, source_url)
            except Exception as exc:
                data = _empty_recipe_data()
                print(f"[pdf] extraction failed for {title!r}: {exc}", flush=True)
            data.update({
                "status": "pending",
                "source_url": source_url,
                "book_title": book_title,
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
