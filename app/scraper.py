"""Dual-mode scraper: Instagram via instaloader, generic via httpx+BeautifulSoup."""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import settings


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
    """Fetch recipe content from any URL.

    Routes Instagram URLs to instaloader, everything else to the generic
    HTTP scraper. This is a synchronous wrapper that delegates to the async
    implementation internally.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower().replace("www.", "")

    if host == "instagram.com":
        return _fetch_instagram(url)
    return _fetch_generic(url)


# ── Instagram fetcher ─────────────────────────────────────────────────────

def _fetch_instagram(url: str) -> ScrapedContent:
    """Fetch an Instagram post's caption and image using instaloader."""
    try:
        from instaloader import Instaloader, Post, BadResponseException, QueryReturnedNotFoundException

        loader = Instaloader(
            download_pictures=True,
            download_videos=False,
            download_video_thumbnails=False,
            save_metadata=False,
            compress_json=False,
            quiet=True,
        )

        # Load cached session or login fresh
        try:
            loader.load_session_from_file(settings.IG_USERNAME)
        except FileNotFoundError:
            loader.login(settings.IG_USERNAME, settings.IG_PASSWORD)
            loader.save_session_to_file()

        # Extract shortcode from URL
        match = re.search(r"(?:p|reel)/([A-Za-z0-9_-]+)", url)
        if not match:
            raise ScrapeError(f"Could not parse Instagram shortcode from {url}")

        shortcode = match.group(1)
        post = Post.from_shortcode(loader.context, shortcode)

        caption = post.caption or ""
        if post.caption_hashtags:
            caption += "\n" + " ".join(post.caption_hashtags)

        # Download image
        image_path = None
        photos_dir = Path(settings.PHOTOS_DIR)
        photos_dir.mkdir(parents=True, exist_ok=True)

        if post.is_video:
            # For reels, grab the thumbnail
            if post.url:
                target = photos_dir / f"{shortcode}.jpg"
                loader.download_pic(target, post.url, post.date_utc)
                image_path = str(target)
        else:
            target = photos_dir / shortcode
            loader.download_post(post, target=shortcode)

        return ScrapedContent(
            text=_clean_text(caption),
            image_path=image_path,
            source_url=url,
        )

    except (QueryReturnedNotFoundException, BadResponseException) as e:
        raise ScrapeError(f"Instagram post not found or private: {e}")
    except Exception as e:
        raise ScrapeError(f"Instagram fetch failed: {e}")


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

    # Remove non-content elements
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # Try to find the main content area
    content = soup.find("article") or soup.find("main") or soup.find("body")
    if content:
        text = content.get_text(separator="\n", strip=True)
    else:
        text = soup.get_text(separator="\n", strip=True)

    # Try to download the first large image
    image_path = None
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src and not src.startswith("data:"):
            try:
                img_url = src if src.startswith("http") else _resolve_url(url, src)
                img_response = httpx.get(img_url, timeout=10.0)
                img_response.raise_for_status()

                if len(img_response.content) > 5000:  # skip tiny images/icons
                    import hashlib
                    slug = hashlib.md5(url.encode()).hexdigest()[:12]
                    photos_dir = Path(settings.PHOTOS_DIR)
                    photos_dir.mkdir(parents=True, exist_ok=True)
                    ext = _guess_image_ext(img_url, img_response.headers.get("content-type"))
                    dest = photos_dir / f"{slug}{ext}"
                    dest.write_bytes(img_response.content)
                    image_path = str(dest)
                    break
            except Exception:
                continue  # image fetch failures are non-fatal

    return ScrapedContent(
        text=_clean_text(text),
        image_path=image_path,
        source_url=url,
    )


# ── Helpers ────────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """Collapse whitespace, strip empty lines."""
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _resolve_url(base_url: str, img_src: str) -> str:
    """Resolve a relative image URL against the page base URL."""
    from urllib.parse import urljoin
    return urljoin(base_url, img_src)


def _guess_image_ext(url: str, content_type: str | None) -> str:
    """Guess file extension from URL or content-type."""
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
