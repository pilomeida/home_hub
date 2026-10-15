"""User-facing privacy notices (Pedro, 2026-10-14)."""

SCAN_NOTICE = (
    "Heads-up: for photos and scans, privacy protection is less reliable. "
    "The Hub can't hide personal details inside an image, so they may be seen "
    "by the AI service that reads it."
)

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".tif", ".tiff", ".bmp")


def is_image_name(filename: str) -> bool:
    return filename.lower().endswith(_IMAGE_EXTS)
