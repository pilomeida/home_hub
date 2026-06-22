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


def sample_recipe_indices(all_recipes: list[dict], n: int = 3) -> list[int]:
    """Return up to n random unique indices into all_recipes."""
    count = min(n, len(all_recipes))
    return random.sample(range(len(all_recipes)), count)


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


# ── Async: vision pass ────────────────────────────────────────────────────────

async def vision_pass(pdf_path: str, page_nums: list[int]) -> dict[int, str]:
    """Render sparse pages as images and extract text via Claude vision."""
    if not page_nums:
        return {}

    all_images = convert_from_path(
        pdf_path,
        first_page=min(page_nums),
        last_page=max(page_nums),
        dpi=150,
    )
    page_images: dict[int, object] = {}
    for offset, img in enumerate(all_images):
        page_num = min(page_nums) + offset
        if page_num in page_nums:
            page_images[page_num] = img

    results: dict[int, str] = {}
    client = _get_client()
    for page_num, img in page_images.items():
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.standard_b64encode(buf.getvalue()).decode()
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
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


# ── Async: boundary detection ─────────────────────────────────────────────────

_BOUNDARY_PROMPT = """You are analyzing a cookbook PDF. Below is the extracted text from each page.
Identify every distinct recipe and list which pages contain its content.

Rules:
- Pages are NOT exclusive — one page can belong to multiple recipes
- Include ALL pages contributing to a recipe (title, ingredients, method, macros, photo pages with overlaid text)
- Skip non-recipe pages (TOC, introduction, acknowledgements, blank pages)
- Recipe title must match exactly what appears in the book

Return ONLY a valid JSON array:
[{{"recipe_title": "Name", "pages": [20, 21]}}, ...]

Page texts:
---
{page_texts}
---"""


async def detect_recipe_boundaries(page_texts: dict[int, str]) -> list[dict]:
    """Single Claude call to identify which pages belong to which recipe."""
    formatted = "\n\n".join(
        f"[Page {num}]\n{text}"
        for num, text in sorted(page_texts.items())
        if text.strip()
    )
    prompt = _BOUNDARY_PROMPT.format(page_texts=formatted[:40000])
    client = _get_client()
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        temperature=0,
        system="You are a precise cookbook analyzer. Return only valid JSON arrays.",
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


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
    """Run the full PDF pipeline and return a session with extracted recipes."""
    sid = session_id or str(uuid.uuid4())[:8]

    # Step 1: text pass
    page_texts_raw = extract_page_texts(pdf_path)

    # Step 2: vision pass on sparse pages
    sparse = flag_sparse_pages(page_texts_raw)
    vision_texts = await vision_pass(pdf_path, sparse)

    # Merge: supplement existing text with vision output, prefer existing text
    full_texts: dict[int, str] = {num: pt.text for num, pt in page_texts_raw.items()}
    for num, vtext in vision_texts.items():
        existing = full_texts.get(num, "")
        full_texts[num] = (existing + "\n" + vtext).strip() if existing else vtext

    # Step 3: boundary detection
    raw_boundaries = await detect_recipe_boundaries(full_texts)
    all_recipes = [{**r, "status": "pending"} for r in raw_boundaries]

    # Step 4: random sample of 3
    sampled_indices = sample_recipe_indices(all_recipes, n=3)

    session = PdfIngestionSession(
        session_id=sid,
        pdf_path=pdf_path,
        book_title=book_title,
        book_slug=book_slug,
        all_recipes=all_recipes,
        sampled_indices=sampled_indices,
        extracted={},
        extraction_complete=False,
    )
    save_session(session)

    # Step 5: per-recipe extraction
    from app.extractor import extract_recipe
    for idx in sampled_indices:
        recipe_meta = all_recipes[idx]
        text = assemble_recipe_text(full_texts, recipe_meta["pages"])
        source_url = f"pdf:{book_slug}#{_slugify(recipe_meta['recipe_title'])}"
        try:
            data = await extract_recipe(text, source_url)
        except Exception:
            data = {k: None for k in (
                "dish_name", "distinguishing_feature", "type", "subtype",
                "macro_tags", "calories_per_portion", "ingredients",
                "prep_time_minutes", "cook_time_minutes", "portions",
                "instructions", "cooking_types", "protein_g", "fat_g",
                "carbs_g", "fiber_g",
            )}
            data["missing_critical_info"] = True
            data["macro_tags"] = []
            data["ingredients"] = []
            data["cooking_types"] = []

        data["status"] = "pending"
        data["source_url"] = source_url
        data["book_title"] = book_title
        session.extracted[str(idx)] = data

    session.extraction_complete = True
    save_session(session)
    return session
