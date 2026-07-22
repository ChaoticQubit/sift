"""Runtime configuration (env-driven). DESIGN.md §12; CLAUDE.md rule 8."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIFT_", env_file=".env", extra="ignore")

    vault_path: Path
    work_dir: Path = Path(".sift")

    # Agno storage (sessions + memory). SQLite local; Postgres is the upgrade path.
    db_url: str = "sqlite:///.sift/sift.db"

    # Models (provider-swappable; can be local)
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-5"
    embed_provider: str = "openai"
    embed_model: str = "text-embedding-3-small"

    # Ingestion
    cookie_browser: str = "chrome"  # gallery-dl --cookies-from-browser
    fetch_cadence: str = "weekly"   # weekly (default) | daily (ceiling). NEVER hourly.

    # Feature flags. instagrapi stays OFF and unused — it is a banned dependency (CLAUDE.md rule 1).
    enable_instagrapi: bool = False
    enable_frame_ocr: bool = False
    enable_translation: bool = False


def load_settings() -> "Settings":
    return Settings()  # type: ignore[call-arg]
