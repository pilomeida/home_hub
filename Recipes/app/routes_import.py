"""PDF cookbook import routes."""

import json
import random
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app.database import get_session
from app.linker import detect_and_link
from app.units import convert_ingredients
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


# ── Clear test recipes ────────────────────────────────────────────────────────
# Must be registered BEFORE /{sid} routes to avoid FastAPI treating "clear" as a sid.

@router.post("/clear")
async def clear_book_recipes(request: Request, db: Session = Depends(get_session)):
    """Delete all recipes imported from a given book (by source_title + slug prefix)."""
    form = await request.form()
    book_title = str(form.get("book_title", ""))
    book_slug = str(form.get("book_slug", ""))
    if not book_slug:
        return JSONResponse({"error": "book_slug required"}, status_code=400)

    candidates = db.exec(
        select(Recipe).where(Recipe.source_title == book_title)
    ).all()
    matching = [r for r in candidates if r.source_url.startswith(f"pdf:{book_slug}#")]

    deleted = 0
    for r in matching:
        if r.photo_path:
            Path(r.photo_path).unlink(missing_ok=True)
        db.delete(r)
        deleted += 1
    db.commit()
    return JSONResponse({"deleted": deleted})


@router.post("")
async def start_import(
    request: Request,
    background_tasks: BackgroundTasks,
    pdf_file: UploadFile = File(...),
    book_title: str = Form(default=""),
    test_mode: str = Form(default="off"),
    sample_pages_raw: str = Form(default="", alias="sample_pages"),
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
    is_test = test_mode == "on"

    from app.pdf_extractor import is_image_pdf
    use_vision = is_image_pdf(str(dest))

    placeholder = PdfIngestionSession(
        session_id=sid, pdf_path=str(dest), book_title=title,
        book_slug=slug, all_recipes=[], sampled_indices=[],
        extracted={}, extraction_complete=False,
        total_pages=0, used_windows=[], sample_pages=[],
        pipeline="vision" if use_vision else "text",
        test_mode=is_test,
    )
    save_session(placeholder)

    if use_vision:
        background_tasks.add_task(
            _run_vision_pipeline, sid, str(dest), title, slug, is_test
        )
    else:
        sample_pages = [int(x) for x in sample_pages_raw.replace(",", " ").split() if x.strip().isdigit()]
        background_tasks.add_task(_run_pipeline, sid, str(dest), title, slug, sample_pages)

    return RedirectResponse(url=f"/import/{sid}", status_code=303)


async def _run_vision_pipeline(
    session_id: str, pdf_path: str, book_title: str, book_slug: str, test_mode: bool
):
    from app.pdf_extractor import create_vision_session
    await create_vision_session(
        pdf_path, book_title, book_slug,
        n_sample=5, test_mode=test_mode, session_id=session_id,
    )


async def _run_pipeline(session_id: str, pdf_path: str, book_title: str, book_slug: str, sample_pages: list[int]):
    session = await create_session(pdf_path, book_title, book_slug, session_id=session_id, sample_pages=sample_pages)

    if sample_pages:
        # Auto-save all extracted recipes directly to DB — no review step needed
        from app.database import engine
        from sqlmodel import Session as DBSession
        with DBSession(engine) as db:
            for idx_str, recipe_data in session.extracted.items():
                if not recipe_data.get("dish_name"):
                    session.extracted[idx_str]["status"] = "skipped"
                    continue
                dish_name = recipe_data["dish_name"]
                distinguisher = recipe_data.get("distinguishing_feature") or None
                source_url = recipe_data.get("source_url", f"pdf:{session.book_slug}#{idx_str}")
                existing = db.exec(select(Recipe).where(Recipe.source_url == source_url)).first()
                if existing:
                    session.extracted[idx_str]["status"] = "approved"
                    session.extracted[idx_str]["saved_recipe_id"] = existing.id
                    continue
                recipe = Recipe(
                    title=_build_title(dish_name, distinguisher),
                    dish_name=dish_name,
                    distinguisher=distinguisher,
                    type=recipe_data.get("type") or "savory",
                    subtype=recipe_data.get("subtype") or None,
                    notes=recipe_data.get("notes") or None,
                    calories_per_portion=_int_or_none(recipe_data.get("calories_per_portion")),
                    protein_g=_int_or_none(recipe_data.get("protein_g")),
                    fat_g=_int_or_none(recipe_data.get("fat_g")),
                    carbs_g=_int_or_none(recipe_data.get("carbs_g")),
                    fiber_g=_int_or_none(recipe_data.get("fiber_g")),
                    cooking_types=json.dumps(recipe_data.get("cooking_types") or []),
                    macro_tags=json.dumps(recipe_data.get("macro_tags") or []),
                    ingredients=json.dumps(convert_ingredients(recipe_data.get("ingredients") or [])),
                    prep_time=_int_or_none(recipe_data.get("prep_time_minutes")),
                    cook_time=_int_or_none(recipe_data.get("cook_time_minutes")),
                    portions=_int_or_none(recipe_data.get("portions")),
                    instructions=recipe_data.get("instructions") or None,
                    photo_path=recipe_data.get("photo_path") or None,
                    source_url=source_url,
                    source_title=session.book_title or None,
                )
                recipe.compute_derived_fields()
                db.add(recipe)
                db.commit()
                db.refresh(recipe)
                detect_and_link(recipe, db)
                session.extracted[idx_str]["status"] = "approved"
                session.extracted[idx_str]["saved_recipe_id"] = recipe.id
        session.auto_approved = True
        save_session(session)


# ── Progress ──────────────────────────────────────────────────────────────────

@router.get("/{sid}")
async def import_progress(sid: str, request: Request):
    try:
        session = load_session(sid)
    except FileNotFoundError:
        return templates.TemplateResponse(request, "404.html", {}, status_code=404)
    if session.extraction_complete:
        if session.pipeline == "vision" and not session.test_mode:
            return RedirectResponse(url=f"/import/{sid}/bulk", status_code=303)
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
        "auto_approved": session.auto_approved,
        "book_title": session.book_title,
        "total_detected": len(session.all_recipes),
        "extracted_so_far": len(session.extracted),
        "current_recipe": session.current_recipe,
        "test_mode": session.test_mode,
        "pipeline": session.pipeline,
        "error": session.error,
    }


# ── Review ────────────────────────────────────────────────────────────────────

@router.get("/{sid}/review")
async def import_review(sid: str, request: Request):
    try:
        session = load_session(sid)
    except FileNotFoundError:
        return templates.TemplateResponse(request, "404.html", {}, status_code=404)
    if session.auto_approved:
        return RedirectResponse(url="/", status_code=303)
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
            notes=form.get(f"notes_{idx}") or recipe_data.get("notes") or None,
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
            source_title=session.book_title or None,
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


# ── Bulk save ────────────────────────────────────────────────────────────────

@router.post("/{sid}/save-all")
async def save_all(sid: str, request: Request, db: Session = Depends(get_session)):
    """Auto-import all (or a random sample) of extracted recipes without per-recipe review."""
    try:
        session = load_session(sid)
    except FileNotFoundError:
        return templates.TemplateResponse(request, "404.html", {}, status_code=404)

    form = await request.form()
    sample_pct = int(form.get("sample_pct") or 100)

    candidates = [
        (int(k), v) for k, v in session.extracted.items()
        if v.get("status") == "pending" and v.get("dish_name")
    ]
    if sample_pct < 100:
        k = max(1, round(len(candidates) * sample_pct / 100))
        candidates = random.sample(candidates, min(k, len(candidates)))

    for idx, recipe_data in candidates:
        dish_name = recipe_data.get("dish_name") or "Unknown"
        distinguisher = recipe_data.get("distinguishing_feature") or None
        source_url = recipe_data.get("source_url", f"pdf:{session.book_slug}#{idx}")

        existing = db.exec(select(Recipe).where(Recipe.source_url == source_url)).first()
        if existing:
            session.extracted[str(idx)]["status"] = "approved"
            session.extracted[str(idx)]["saved_recipe_id"] = existing.id
            continue

        recipe = Recipe(
            title=_build_title(dish_name, distinguisher),
            dish_name=dish_name,
            distinguisher=distinguisher,
            type=recipe_data.get("type") or "savory",
            subtype=recipe_data.get("subtype") or None,
            notes=recipe_data.get("notes") or None,
            calories_per_portion=_int_or_none(recipe_data.get("calories_per_portion")),
            protein_g=_int_or_none(recipe_data.get("protein_g")),
            fat_g=_int_or_none(recipe_data.get("fat_g")),
            carbs_g=_int_or_none(recipe_data.get("carbs_g")),
            fiber_g=_int_or_none(recipe_data.get("fiber_g")),
            cooking_types=json.dumps(recipe_data.get("cooking_types") or []),
            macro_tags=json.dumps(recipe_data.get("macro_tags") or []),
            ingredients=json.dumps(recipe_data.get("ingredients") or []),
            prep_time=_int_or_none(recipe_data.get("prep_time_minutes")),
            cook_time=_int_or_none(recipe_data.get("cook_time_minutes")),
            portions=_int_or_none(recipe_data.get("portions")),
            instructions=recipe_data.get("instructions") or None,
            photo_path=recipe_data.get("photo_path") or None,
            source_url=source_url,
            source_title=session.book_title or None,
        )
        recipe.compute_derived_fields()
        db.add(recipe)
        db.commit()
        db.refresh(recipe)
        detect_and_link(recipe, db)
        session.extracted[str(idx)]["status"] = "approved"
        session.extracted[str(idx)]["saved_recipe_id"] = recipe.id

    save_session(session)
    return RedirectResponse(url=f"/import/{sid}/summary", status_code=303)


# ── Bulk approve (full-book) ──────────────────────────────────────────────────

@router.get("/{sid}/bulk")
async def import_bulk(sid: str, request: Request):
    try:
        session = load_session(sid)
    except FileNotFoundError:
        return templates.TemplateResponse(request, "404.html", {}, status_code=404)
    recipes = sorted(
        [(int(k), v) for k, v in session.extracted.items() if v.get("status") == "pending"],
        key=lambda x: x[0],
    )
    return templates.TemplateResponse(request, "import.html", {
        "view": "bulk", "session": session, "sid": sid, "recipes": recipes,
    })


@router.post("/{sid}/bulk")
async def submit_bulk(sid: str, request: Request, db: Session = Depends(get_session)):
    try:
        session = load_session(sid)
    except FileNotFoundError:
        return templates.TemplateResponse(request, "404.html", {}, status_code=404)

    form = await request.form()
    skip_indices = {int(k.replace("skip_", "")) for k in form.keys() if k.startswith("skip_")}

    for idx_str, recipe_data in session.extracted.items():
        if recipe_data.get("status") != "pending":
            continue
        idx = int(idx_str)
        if idx in skip_indices:
            session.extracted[idx_str]["status"] = "skipped"
            continue

        dish_name = recipe_data.get("dish_name") or "Unknown"
        distinguisher = recipe_data.get("distinguishing_feature") or None
        source_url = recipe_data.get("source_url", f"pdf:{session.book_slug}#{idx_str}")

        existing = db.exec(select(Recipe).where(Recipe.source_url == source_url)).first()
        if existing:
            session.extracted[idx_str]["status"] = "approved"
            session.extracted[idx_str]["saved_recipe_id"] = existing.id
            continue

        recipe = Recipe(
            title=_build_title(dish_name, distinguisher),
            dish_name=dish_name,
            distinguisher=distinguisher,
            type=recipe_data.get("type") or "savory",
            subtype=recipe_data.get("subtype") or None,
            notes=recipe_data.get("notes") or None,
            calories_per_portion=_int_or_none(recipe_data.get("calories_per_portion")),
            protein_g=_int_or_none(recipe_data.get("protein_g")),
            fat_g=_int_or_none(recipe_data.get("fat_g")),
            carbs_g=_int_or_none(recipe_data.get("carbs_g")),
            fiber_g=_int_or_none(recipe_data.get("fiber_g")),
            cooking_types=json.dumps(recipe_data.get("cooking_types") or []),
            macro_tags=json.dumps(recipe_data.get("macro_tags") or []),
            ingredients=json.dumps(recipe_data.get("ingredients") or []),
            prep_time=_int_or_none(recipe_data.get("prep_time_minutes")),
            cook_time=_int_or_none(recipe_data.get("cook_time_minutes")),
            portions=_int_or_none(recipe_data.get("portions")),
            instructions=recipe_data.get("instructions") or None,
            photo_path=recipe_data.get("photo_path") or None,
            source_url=source_url,
            source_title=session.book_title or None,
        )
        recipe.compute_derived_fields()
        db.add(recipe)
        db.commit()
        db.refresh(recipe)
        detect_and_link(recipe, db)
        session.extracted[idx_str]["status"] = "approved"
        session.extracted[idx_str]["saved_recipe_id"] = recipe.id

    save_session(session)
    return RedirectResponse(url=f"/import/{sid}/summary", status_code=303)


# ── Import more ───────────────────────────────────────────────────────────────

@router.post("/{sid}/more")
async def import_more(sid: str, background_tasks: BackgroundTasks):
    # With full-book extraction, all recipes are already extracted upfront.
    # This route is kept for sessions created by older code that used window sampling.
    return RedirectResponse(url=f"/import/{sid}/summary", status_code=303)
