"""A paper beside what the Hub read (PLAN step 11c): the small first-page preview every page about one paper shows,
and the picture behind it. The paper itself is only ever served by its own file route (`file_url`); the preview of a
PDF is its first page drawn on the server by poppler's `pdftoppm` (already used for the PDF text), cached under
`data/previews/`, and never sent anywhere else. Rules for the look: docs/STYLE.md section 4."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.categories.base import MediaKind
from app.categories.fields import file_url, media_kind_for
from app.config import settings
from app.models.document import Document

PREVIEW_WIDTH = 720                    # pixels across; the preview is shown at most ~16rem wide, sharp on a 2x screen
_BROWSER_IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}    # HEIC is an image no browser draws


@dataclass(frozen=True)
class PaperView:
    """What a page needs to show one paper. `kind` is "pdf", "image" or "video" when it can be shown; otherwise
    `problem` says what, why and what to do (the Messages rule), and the page shows that instead."""
    document_id: int
    filename: str
    file_url: Optional[str]
    kind: Optional[str]
    preview_url: Optional[str]
    problem: Optional[dict]


def _no_preview(why: str, next_step: str) -> dict:
    return {"what": "No preview.", "why": why, "next_step": next_step}


def file_path(document: Document) -> Optional[Path]:
    """The paper's file on the server, looked up the way its file route serves it (by name in the documents
    folder), or None for a source with no file."""
    if not document.file_path:
        return None
    stored = Path(document.file_path)
    return stored if stored.is_file() else settings.DOCUMENTS_DIR / stored.name


def paper_view(document: Optional[Document]) -> Optional[PaperView]:
    """None for no document, or for the bank feed's statement (no paper exists, and its page already says so)."""
    if document is None or not document.file_path:
        return None
    url = file_url(document)
    path = file_path(document)
    base = dict(document_id=document.id, filename=document.filename, file_url=url)
    if path is None or not path.is_file():
        return PaperView(**base, kind=None, preview_url=None, problem=_no_preview(
            "The paper's file is not on the server, so there is nothing to show.",
            "Add the paper again with “+ Add a paper”; what the Hub read stays as it is."))
    if document.password_protected:
        return PaperView(**base, kind=None, preview_url=None, problem=_no_preview(
            "The paper is locked with a password, so the Hub can't draw it.", "Open the file to see it."))
    media = media_kind_for(document.filename or path.name)
    if media == MediaKind.PDF:
        return PaperView(**base, kind="pdf", preview_url=f"/documents/{document.id}/preview.png", problem=None)
    if media == MediaKind.IMAGE and path.suffix.lower() in _BROWSER_IMAGES:
        return PaperView(**base, kind="image", preview_url=url, problem=None)
    if media == MediaKind.VIDEO:
        return PaperView(**base, kind="video", preview_url=None, problem=None)
    return PaperView(**base, kind=None, preview_url=None, problem=_no_preview(
        f"A {path.suffix.lstrip('.').upper() or 'file'} of this kind can't be drawn in the browser.",
        "Open the file to see it."))


def previews_dir() -> Path:
    return Path(settings.DATABASE_PATH).parent / "previews"


def first_page_png(document: Document) -> Optional[Path]:
    """The cached picture of a PDF's first page, drawn on first use. None when the file is missing, is not a PDF,
    is locked, or poppler can't draw it (the page then says so)."""
    view = paper_view(document)
    if view is None or view.kind != "pdf":
        return None
    source = file_path(document)
    target = previews_dir() / f"{Path(document.file_path).stem}.p1.png"
    if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    exe = shutil.which("pdftoppm")
    if exe is None:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    prefix = target.with_suffix("").with_suffix("")          # pdftoppm adds ".png" itself with -singlefile
    try:
        done = subprocess.run([exe, "-png", "-f", "1", "-l", "1", "-singlefile", "-scale-to", str(PREVIEW_WIDTH),
                               str(source), str(prefix) + ".p1"], capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return target if done.returncode == 0 and target.is_file() else None
