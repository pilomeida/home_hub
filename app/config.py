"""Application configuration from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def parse_sender_allowlist(raw: str) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def parse_telegram_users(raw: str) -> dict[int, str]:
    """"111:Pedro,222:Rute" -> {111: "Pedro", 222: "Rute"}."""
    users: dict[int, str] = {}
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        user_id, _, name = part.partition(":")
        if not user_id.strip().isdigit() or not name.strip():
            raise ValueError(f"HUB_TELEGRAM_ALLOWED_USERS: bad entry {part!r} (expected <id>:<name>)")
        users[int(user_id)] = name.strip()
    return users


class Settings:
    ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
    DATABASE_PATH: Path = Path(os.environ.get("DATABASE_PATH") or "data/home_family.db")
    DOCUMENTS_DIR: Path = Path(os.environ.get("DOCUMENTS_DIR") or "app/static/documents")
    CF_ACCESS_TEAM_DOMAIN: str = os.environ.get("CF_ACCESS_TEAM_DOMAIN") or ""
    CF_ACCESS_AUD: str = os.environ.get("CF_ACCESS_AUD") or ""

    # --- Ingestion channels (all optional; the web app never needs them) ---
    HUB_IMAP_HOST: str = os.environ.get("HUB_IMAP_HOST") or ""
    HUB_IMAP_PORT: int = int(os.environ.get("HUB_IMAP_PORT") or 993)
    HUB_IMAP_USER: str = os.environ.get("HUB_IMAP_USER") or ""
    HUB_IMAP_PASSWORD: str = os.environ.get("HUB_IMAP_PASSWORD") or ""
    HUB_TELEGRAM_BOT_TOKEN: str = os.environ.get("HUB_TELEGRAM_BOT_TOKEN") or ""
    PUBLIC_BASE_URL: str = os.environ.get("PUBLIC_BASE_URL") or "https://hub.cdafamily.casa"

    @property
    def imap_configured(self) -> bool:
        return bool(self.HUB_IMAP_HOST and self.HUB_IMAP_USER and self.HUB_IMAP_PASSWORD)

    @property
    def imap_allowed_senders(self) -> frozenset[str]:
        return parse_sender_allowlist(os.environ.get("HUB_IMAP_ALLOWED_SENDERS") or "")

    @property
    def telegram_allowed_users(self) -> dict[int, str]:
        return parse_telegram_users(os.environ.get("HUB_TELEGRAM_ALLOWED_USERS") or "")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.DATABASE_PATH}"


settings = Settings()
