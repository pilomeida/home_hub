"""Application configuration from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Settings:
    TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
    ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
    ALLOWED_TELEGRAM_USER_ID: int = int(os.environ["ALLOWED_TELEGRAM_USER_ID"])
    VPS_IP: str = os.environ.get("VPS_IP", "127.0.0.1")
    DATABASE_PATH: Path = Path(os.environ.get("DATABASE_PATH", "data/recipes.db"))
    PHOTOS_DIR: Path = Path(os.environ.get("PHOTOS_DIR", "app/static/photos"))

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.DATABASE_PATH}"


settings = Settings()
