"""Hybrid PDF extraction: pdfplumber text pass + Claude vision fallback."""

import base64
import io
import json
import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pdfplumber
from pdf2image import convert_from_path

from app.config import settings

_MIN_TEXT_CHARS = 50
_SESSIONS_DIR = Path("data/import_sessions")

_anthropic_client = None


def _get_client():
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import AsyncAnthropic
        _anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client


@dataclass
class PageText:
    page_num: int
    text: str
    from_vision: bool = False


# ── Sync helpers ──────────────────────────────────────────────────────────────

def extract_page_texts(pdf_path: str) -> dict[int, PageText]:
    """Extract text from every page of a PDF via pdfplumber."""
    result = {}
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = (page.extract_text() or "").strip()
            result[i + 1] = PageText(page_num=i + 1, text=text)
    return result


def flag_sparse_pages(page_texts: dict[int, PageText]) -> list[int]:
    """Return page numbers whose text is shorter than _MIN_TEXT_CHARS."""
    return [num for num, pt in page_texts.items() if len(pt.text) < _MIN_TEXT_CHARS]


def assemble_recipe_text(page_texts: dict[int, str], pages: list[int]) -> str:
    """Concatenate page texts in sorted order, deduped."""
    parts = []
    for p in sorted(set(pages)):
        if page_texts.get(p):
            parts.append(f"[Page {p}]\n{page_texts[p]}")
    return "\n\n".join(parts)


def _slugify(text: str) -> str:
    """Convert a string to a URL-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def sample_page_windows(
    total_pages: int,
    n: int = 3,
    window_size: int = 3,
    exclude_pages: set[int] | None = None,
) -> list[list[int]]:
    """Sample n windows of window_size consecutive pages, distributed across the book.

    Skips the first 10 pages (usually front matter/TOC). Avoids pages in exclude_pages.
    Returns fewer than n windows if not enough valid pages remain.
    """
    exclude = exclude_pages or set()
    skip_front = 10
    usable_start = skip_front + 1
    usable_end = total_pages - window_size + 1

    if usable_end < usable_start:
        return []

    # All valid window start positions (window doesn't overlap excluded pages)
    valid_starts = [
        p for p in range(usable_start, usable_end + 1)
        if not any((p + i) in exclude for i in range(window_size))
    ]

    if not valid_starts:
        return []

    # Divide valid positions into n segments and pick one start per segment
    seg = len(valid_starts) // n
    if seg == 0:
        seg = 1

    windows = []
    for i in range(n):
        chunk = valid_starts[i * seg: (i + 1) * seg if i < n - 1 else len(valid_starts)]
        if chunk:
            start = random.choice(chunk)
            windows.append(list(range(start, start + window_size)))

    return windows


# ── Async: vision pass ────────────────────────────────────────────────────────

async def vision_pass(pdf_path: str, page_nums: list[int]) -> dict[int, str]:
    """Render pages one-at-a-time as images and extract text via Claude vision."""
    if not page_nums:
        return {}

    results: dict[int, str] = {}
    client = _get_client()
    for page_num in page_nums:
        images = convert_from_path(pdf_path, first_page=page_num, last_page=page_num, dpi=100)
        if not images:
            continue
        buf = io.BytesIO()
        images[0].save(buf, format="PNG")
        b64 = base64.standard_b64encode(buf.getvalue()).decode()
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": (
                        "Extract ALL text visible in this cookbook page. "
                        "Preserve numbers and units exactly. "
                        "Return only the extracted text, nothing else."
                    )},
                ],
            }],
        )
        results[page_num] = message.content[0].text.strip()
    return results


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
    used_windows: list = field(default_factory=list)


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


# ── Window extraction ─────────────────────────────────────────────────────────

async def _build_window_texts(
    pdf_path: str,
    page_texts_raw: dict[int, PageText],
    pages: list[int],
) -> dict[int, str]:
    """Run vision on sparse pages within a window and return merged texts."""
    sparse_set = set(flag_sparse_pages(page_texts_raw))
    vision_pages = [p for p in pages if p in sparse_set]
    vision_texts = await vision_pass(pdf_path, vision_pages)

    full_texts: dict[int, str] = {}
    for p in pages:
        base = page_texts_raw[p].text if p in page_texts_raw else ""
        vision = vision_texts.get(p, "")
        full_texts[p] = (base + "\n" + vision).strip() if base else vision
    return full_texts


async def _extract_window(
    full_texts: dict[int, str],
    pages: list[int],
    book_slug: str,
    book_title: str,
) -> list[dict]:
    """Extract all complete recipes from a page window. Returns list (possibly empty)."""
    from app.extractor import extract_recipes_from_chunk
    chunk_text = assemble_recipe_text(full_texts, pages)
    if not chunk_text.strip():
        return []
    recipes = await extract_recipes_from_chunk(chunk_text)
    result = []
    for recipe in recipes:
        source_url = f"pdf:{book_slug}#{_slugify(recipe.get('dish_name', ''))}"
        recipe.update({"status": "pending", "source_url": source_url, "book_title": book_title})
        result.append(recipe)
    return result


# ── Full pipeline ─────────────────────────────────────────────────────────────

async def create_session(
    pdf_path: str,
    book_title: str,
    book_slug: str,
    session_id: str | None = None,
) -> PdfIngestionSession:
    """Sample 3×3-page windows, run vision on sparse pages, extract complete recipes."""
    sid = session_id or str(uuid.uuid4())[:8]
    print(f"[pdf] session {sid} starting: {book_title!r}", flush=True)

    # Step 1: text pass on all pages
    print(f"[pdf] text pass: {pdf_path}", flush=True)
    page_texts_raw = extract_page_texts(pdf_path)
    total_pages = len(page_texts_raw)
    print(f"[pdf] {total_pages} pages total", flush=True)

    # Step 2: pick 3 windows of 3 consecutive pages
    windows = sample_page_windows(total_pages, n=3, window_size=3)
    print(f"[pdf] windows: {windows}", flush=True)

    all_recipes: list[dict] = []
    error: Optional[str] = None

    try:
        for i, window in enumerate(windows):
            print(f"[pdf] window {i+1}/{len(windows)}: pages {window}", flush=True)
            full_texts = await _build_window_texts(pdf_path, page_texts_raw, window)
            recipes = await _extract_window(full_texts, window, book_slug, book_title)
            print(f"[pdf] window {i+1}: found {len(recipes)} complete recipe(s)", flush=True)
            all_recipes.extend(recipes)
    except Exception as exc:
        error = str(exc)
        print(f"[pdf] session {sid} error: {exc}", flush=True)

    session = PdfIngestionSession(
        session_id=sid,
        pdf_path=pdf_path,
        book_title=book_title,
        book_slug=book_slug,
        all_recipes=all_recipes,
        sampled_indices=list(range(len(all_recipes))),
        extracted={str(i): r for i, r in enumerate(all_recipes)},
        extraction_complete=True,
        total_pages=total_pages,
        used_windows=windows,
        error=error,
    )
    save_session(session)
    print(f"[pdf] session {sid} complete: {len(all_recipes)} recipe(s) found", flush=True)
    return session
