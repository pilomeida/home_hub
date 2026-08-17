"""Saves uploaded files to disk and computes content hashes for dedup."""

import hashlib
import uuid
from pathlib import Path

from app.config import settings


def save_upload(filename: str, content: bytes) -> tuple[str, str]:
    """Save `content` under DOCUMENTS_DIR with a unique name, returning
    (file_path, content_hash)."""
    settings.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256(content).hexdigest()
    suffix = Path(filename).suffix
    unique_name = f"{uuid.uuid4().hex}{suffix}"
    file_path = settings.DOCUMENTS_DIR / unique_name
    file_path.write_bytes(content)
    return str(file_path), content_hash
