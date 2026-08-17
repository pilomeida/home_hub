"""Application configuration from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Settings:
    ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
    DATABASE_PATH: Path = Path(os.environ.get("DATABASE_PATH", "data/home_family.db"))
    DOCUMENTS_DIR: Path = Path(os.environ.get("DOCUMENTS_DIR", "app/static/documents"))
    CF_ACCESS_TEAM_DOMAIN: str = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "")
    CF_ACCESS_AUD: str = os.environ.get("CF_ACCESS_AUD", "")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.DATABASE_PATH}"


settings = Settings()
