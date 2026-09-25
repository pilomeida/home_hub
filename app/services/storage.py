"""Saves uploaded files to disk and computes content hashes for dedup."""

import hashlib
import uuid
from pathlib import Path

from app.config import settings


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def save_file(filename: str, content: bytes) -> str:
    """Write `content` under DOCUMENTS_DIR with a unique name (keeping the
    original extension) and return the file path."""
    settings.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    unique_name = f"{uuid.uuid4().hex}{Path(filename).suffix}"
    file_path = settings.DOCUMENTS_DIR / unique_name
    file_path.write_bytes(content)
    return str(file_path)


def save_upload(filename: str, content: bytes) -> tuple[str, str]:
    """Save and hash in one call -- kept for the one-off scripts in scripts/.
    Application code goes through app.services.ingestion.receive_file, which
    hashes BEFORE writing so a duplicate never leaves an orphan file."""
    return save_file(filename, content), content_hash(content)