"""Builds Claude content blocks (document or image) for an uploaded file."""

import base64
from pathlib import Path

_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class UnsupportedFileTypeError(Exception):
    pass


def build_content_block(file_path: str) -> dict:
    """Return a Claude content block for the given file: a `document` block
    for PDFs (the whole file, all pages) or an `image` block for image
    files."""
    path = Path(file_path)
    suffix = path.suffix.lower()
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
