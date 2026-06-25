"""Generic URL scraper using httpx + BeautifulSoup."""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import settings

logger = logging.getLogger(__name__)

_MIN_IMAGE_BYTES = 5000


class ScrapeError(Exception):
    """Raised when content cannot be fetched from a URL."""
    pass


@dataclass
class ScrapedContent:
    text: str
    image_path: str | None
    source_url: str


# ── Public API ────────────────────────────────────────────────────────────

def fetch_content(url: str) -> ScrapedContent:
    """Fetch recipe content from any URL via HTTP."""
    return _fetch_generic(url)


# ── Generic URL fetcher ────────────────────────────────────────────────────

def _fetch_generic(url: str) -> ScrapedContent:
    """Fetch and extract text content from any URL."""
    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=15.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; RecipeBot/1.0)"},
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise ScrapeError(f"HTTP error fetching {url}: {e}")

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    content = soup.find("article") or soup.find("main") or soup.find("body")
    if content:
        text = content.get_text(separator="\n", strip=True)
    else:
        text = soup.get_text(separator="\n", strip=True)

    image_path = None
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src and not src.startswith("data:"):
            try:
                img_url = src if src.startswith("http") else urljoin(url, src)
                img_response = httpx.get(img_url, timeout=10.0)
                img_response.raise_for_status()

                if len(img_response.content) > _MIN_IMAGE_BYTES:
                    slug = hashlib.md5(url.encode()).hexdigest()[:12]
                    photos_dir = Path(settings.PHOTOS_DIR)
                    photos_dir.mkdir(parents=True, exist_ok=True)
                    ext = _guess_image_ext(img_url, img_response.headers.get("content-type"))
                    dest = photos_dir / f"{slug}{ext}"
                    dest.write_bytes(img_response.content)
                    image_path = str(dest)
                    break
            except Exception:
                continue

    return ScrapedContent(
        text=_clean_text(text),
        image_path=image_path,
        source_url=url,
    )


# ── Helpers ────────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _guess_image_ext(url: str, content_type: str | None) -> str:
    if content_type:
        if "jpeg" in content_type or "jpg" in content_type:
            return ".jpg"
        if "png" in content_type:
            return ".png"
        if "webp" in content_type:
            return ".webp"
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        if path.endswith(ext):
            return ext
    return ".jpg"
