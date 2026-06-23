"""PDF cookbook import routes."""

import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app.database import get_session
from app.linker import detect_and_link
from app.main import templates
from app.models import Recipe
from app.pdf_extractor import (
    PdfIngestionSession,
    _SESSIONS_DIR,
    _slugify,
    create_session,
    get_pending_batch,
    load_session,
    save_session,
)

router = APIRouter(prefix="/import", tags=["import"])

_PDF_UPLOAD_DIR = Path("data/pdf_uploads")


def _build_title(dish_name: str, distinguisher: str | None) -> str:
    return f"{dish_name} ({distinguisher})" if distinguisher else dish_name


def _int_or_none(val) -> int | None:
    try:
        return int(float(val)) if val not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


# ── Upload ────────────────────────────────────────────────────────────────────

@router.get("")
async def import_page(request: Request):
    return templates.TemplateResponse(request, "import.html", {
        "view": "upload", "error": None,
    })


@router.post("")
async def start_import(
    request: Request,
    background_tasks: BackgroundTasks,
    pdf_file: UploadFile = File(...),
    book_title: str = Form(default=""),
):
    if pdf_file.content_type != "application/pdf":
        return templates.TemplateResponse(request, "import.html", {
            "view": "upload", "error": "Only PDF files are accepted.",
        })
    _PDF_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    sid = str(uuid.uuid4())[:8]
    dest = _PDF_UPLOAD_DIR / f"{sid}.pdf"
    with dest.open("wb") as f:
        shutil.copyfileobj(pdf_file.file, f)

    slug = _slugify(book_title or Path(pdf_file.filename).stem)
    title = book_title or Path(pdf_file.filename).stem

    # Create placeholder session so the progress page can load immediately
    placeholder = PdfIngestionSession(
        session_id=sid, pdf_path=str(dest), book_title=title,
        book_slug=slug, all_recipes=[], sampled_indices=[],
        extracted={}, extraction_complete=False,
        total_pages=0, used_windows=[],
    )
    save_session(placeholder)

    background_tasks.add_task(_run_pipeline, sid, str(dest), title, slug)
    return RedirectResponse(url=f"/import/{sid}", status_code=303)


async def _run_pipeline(session_id: str, pdf_path: str, book_title: str, book_slug: str):
    await create_session(pdf_path, book_title, book_slug, session_id=session_id)


# ── Progress ──────────────────────────────────────────────────────────────────

@router.get("/{sid}")
async def import_progress(sid: str, request: Request):
    try:
        session = load_session(sid)
    except FileNotFoundError:
        return templates.TemplateResponse(request, "404.html", {}, status_code=404)
    if session.extraction_complete:
        return RedirectResponse(url=f"/import/{sid}/review", status_code=303)
    return templates.TemplateResponse(request, "import.html", {
        "view": "progress", "session": session, "sid": sid,
    })


@router.get("/{sid}/status")
async def import_status(sid: str):
    try:
        session = load_session(sid)
    except FileNotFoundError:
        return JSONResponse({"error": "session not found"}, status_code=404)
    return {
        "extraction_complete": session.extraction_complete,
        "book_title": session.book_title,
        "total_detected": len(session.all_recipes),
        "extracted_so_far": len(session.extracted),
        "error": session.error,
    }


# ── Review ────────────────────────────────────────────────────────────────────

@router.get("/{sid}/review")
async def import_review(sid: str, request: Request):
    try:
        session = load_session(sid)
    except FileNotFoundError:
        return templates.TemplateResponse(request, "404.html", {}, status_code=404)
    batch = get_pending_batch(session, n=3)
    if not batch:
        return RedirectResponse(url=f"/import/{sid}/summary", status_code=303)
    reviewed = sum(
        1 for v in session.extracted.values()
        if v.get("status") in ("approved", "skipped")
    )
    return templates.TemplateResponse(request, "import.html", {
        "view": "review", "session": session, "sid": sid,
        "batch": batch, "reviewed": reviewed,
        "total_sampled": len(session.sampled_indices),
    })


@router.post("/{sid}/review")
async def submit_review(sid: str, request: Request, db: Session = Depends(get_session)):
    try:
        session = load_session(sid)
    except FileNotFoundError:
        return templates.TemplateResponse(request, "404.html", {}, status_code=404)

    form = await request.form()
    for idx in session.sampled_indices:
        key = str(idx)
        action = form.get(f"action_{idx}")
        if action not in ("save", "skip"):
            continue
        recipe_data = session.extracted.get(key)
        if not recipe_data or recipe_data.get("status") != "pending":
            continue

        if action == "skip":
            session.extracted[key]["status"] = "skipped"
            continue

        dish_name = form.get(f"dish_name_{idx}") or recipe_data.get("dish_name") or "Unknown"
        distinguisher = form.get(f"distinguisher_{idx}") or recipe_data.get("distinguishing_feature") or None
        source_url = recipe_data.get("source_url", f"pdf:{session.book_slug}#unknown")

        existing = db.exec(select(Recipe).where(Recipe.source_url == source_url)).first()
        if existing:
            session.extracted[key]["status"] = "approved"
            session.extracted[key]["saved_recipe_id"] = existing.id
            continue

        recipe = Recipe(
            title=_build_title(dish_name, distinguisher),
            dish_name=dish_name,
            distinguisher=distinguisher,
            type=form.get(f"type_{idx}") or recipe_data.get("type") or "savory",
            subtype=form.get(f"subtype_{idx}") or recipe_data.get("subtype") or None,
            calories_per_portion=_int_or_none(
                form.get(f"calories_{idx}") or recipe_data.get("calories_per_portion")
            ),
            protein_g=_int_or_none(form.get(f"protein_g_{idx}") or recipe_data.get("protein_g")),
            fat_g=_int_or_none(form.get(f"fat_g_{idx}") or recipe_data.get("fat_g")),
            carbs_g=_int_or_none(form.get(f"carbs_g_{idx}") or recipe_data.get("carbs_g")),
            fiber_g=_int_or_none(form.get(f"fiber_g_{idx}") or recipe_data.get("fiber_g")),
            cooking_types=json.dumps(recipe_data.get("cooking_types") or []),
            macro_tags=json.dumps(recipe_data.get("macro_tags") or []),
            ingredients=json.dumps(recipe_data.get("ingredients") or []),
            prep_time=_int_or_none(recipe_data.get("prep_time_minutes")),
            cook_time=_int_or_none(recipe_data.get("cook_time_minutes")),
            portions=_int_or_none(recipe_data.get("portions")),
            instructions=recipe_data.get("instructions") or None,
            photo_path=recipe_data.get("photo_path") or None,
            source_url=source_url,
        )
        recipe.compute_derived_fields()
        db.add(recipe)
        db.commit()
        db.refresh(recipe)
        detect_and_link(recipe, db)
        session.extracted[key]["status"] = "approved"
        session.extracted[key]["saved_recipe_id"] = recipe.id

    save_session(session)
    return RedirectResponse(url=f"/import/{sid}/review", status_code=303)


# ── Summary ───────────────────────────────────────────────────────────────────

@router.get("/{sid}/summary")
async def import_summary(sid: str, request: Request):
    try:
        session = load_session(sid)
    except FileNotFoundError:
        return templates.TemplateResponse(request, "404.html", {}, status_code=404)
    saved = [v for v in session.extracted.values() if v.get("status") == "approved"]
    skipped = [v for v in session.extracted.values() if v.get("status") == "skipped"]
    used_pages = {p for w in session.used_windows for p in w}
    can_import_more = session.total_pages > 0 and (session.total_pages - len(used_pages)) >= 12
    return templates.TemplateResponse(request, "import.html", {
        "view": "summary", "session": session, "sid": sid,
        "saved": saved, "skipped": skipped, "remaining": 1 if can_import_more else 0,
    })


# ── Import more ───────────────────────────────────────────────────────────────

@router.post("/{sid}/more")
async def import_more(sid: str, background_tasks: BackgroundTasks):
    # With full-book extraction, all recipes are already extracted upfront.
    # This route is kept for sessions created by older code that used window sampling.
    return RedirectResponse(url=f"/import/{sid}/summary", status_code=303)
