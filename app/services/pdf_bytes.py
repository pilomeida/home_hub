"""Bank-site downloads are sometimes a serialized Java byte[] wrapping the real
PDF. Strip that header so the stored file is a clean PDF and its content hash
matches clean copies of the same document."""

from typing import Optional

MAX_HEADER = 128
EOF_WINDOW = 2048
NOT_PDF = "only PDF files are accepted"
TRUNCATED = "file looks truncated"


def inspect_pdf(content: bytes) -> tuple[Optional[bytes], int, Optional[str]]:
    """(clean bytes, bytes stripped, problem). Exactly one of bytes/problem is set."""
    if content.startswith(b"%PDF-"):
        return content, 0, None
    idx = content[:MAX_HEADER + 5].find(b"%PDF-")
    if idx < 0 or idx > MAX_HEADER:
        return None, 0, NOT_PDF
    clean = content[idx:]
    if b"%%EOF" not in clean[-EOF_WINDOW:]:
        return None, idx, TRUNCATED
    return clean, idx, None


def normalize_pdf_bytes(content: bytes) -> Optional[bytes]:
    return inspect_pdf(content)[0]
