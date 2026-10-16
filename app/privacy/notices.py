"""User-facing privacy notices (Pedro, 2026-10-14)."""

SCAN_NOTICE = (
    "Heads-up: for photos and scans, privacy protection is less reliable. "
    "The Hub can't hide personal details inside an image, so they may be seen "
    "by the AI service that reads it."
)

# Shown on the Health upload page, after SCAN_NOTICE: which level reads the paper and what that level gives.
HEALTH_NOTICE = (
    "Health papers are always read at the highest privacy level (house_high), whatever the Privacy page says. "
    "Only Privatemode reads them: the paper is encrypted on the Hub's server and opened only inside a verified, "
    "sealed machine, so the AI service cannot see it. If that route is down, the paper is not read, you are told, and nothing is "
    "sent anywhere else."
)

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".tif", ".tiff", ".bmp")


def is_image_name(filename: str) -> bool:
    return filename.lower().endswith(_IMAGE_EXTS)
