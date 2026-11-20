"""Builds Claude content blocks (document or image) for an uploaded file."""

import base64
from io import BytesIO
from pathlib import Path

from app.services.reading import ReadingError, check_size

_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class UnsupportedFileTypeError(ReadingError):
    pass


def build_content_block(file_path: str) -> dict:
    """Return a Claude content block for the given file: a `document` block
    for PDFs (the whole file, all pages) or an `image` block for image
    files."""
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix != ".pdf" and suffix not in _IMAGE_MEDIA_TYPES:
        raise UnsupportedFileTypeError(f"Unsupported file type: {suffix}")
    check_size(file_path)              # PLAN step 5a: refused before upload, with a plain reason
    data = base64.b64encode(path.read_bytes()).decode()

    if suffix == ".pdf":
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": data},
        }
    if suffix in _IMAGE_MEDIA_TYPES:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": _IMAGE_MEDIA_TYPES[suffix], "data": data},
        }
    raise UnsupportedFileTypeError(f"Unsupported file type: {suffix}")


def pdf_page_count(file_path: str) -> int:
    from pypdf import PdfReader
    return len(PdfReader(file_path).pages)


def pdf_pages_block(file_path: str, first_page: int, last_page: int) -> dict:
    """A `document` block holding only pages first_page..last_page (1-based, inclusive) of a PDF: a paper too big
    for one reading is read in parts (PLAN step 9 D11)."""
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(file_path)
    if not 1 <= first_page <= last_page <= len(reader.pages):
        raise ReadingError(f"pages {first_page}-{last_page} are not in this {len(reader.pages)}-page paper")
    writer = PdfWriter()
    for index in range(first_page - 1, last_page):
        writer.add_page(reader.pages[index])
    buffer = BytesIO()
    writer.write(buffer)
    data = base64.b64encode(buffer.getvalue()).decode()
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}


def is_model_readable(file_path: str) -> bool:
    """True if build_content_block can turn this file into a Claude block.
    Channels still store other media (HEIC, video); the classifier skips them."""
    suffix = Path(file_path).suffix.lower()
    return suffix == ".pdf" or suffix in _IMAGE_MEDIA_TYPES
