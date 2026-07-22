"""Runtime configuration (env-driven). DESIGN.md §12; CLAUDE.md rule 8."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIFT_", env_file=".env", extra="ignore")

    vault_path: Path
    # State (SQLite + LanceDB + dedup ledger) lives WITH the data: <vault>/.sift by default.
    work_dir: Path | None = None
    db_url: str | None = None  # Agno storage; SQLite local, Postgres is the upgrade path.

    # Models (provider-swappable; can be local)
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    embed_provider: str = "openai"
    embed_model: str = "text-embedding-3-small"

    # Ingestion
    cookie_browser: str = "chrome"  # gallery-dl --cookies-from-browser
    fetch_cadence: str = "weekly"   # weekly (default) | daily (ceiling). NEVER hourly.

    # Feature flags (deferred features stay off; instagrapi has NO flag — banned outright, CLAUDE.md rule 1)
    enable_frame_ocr: bool = False
    enable_translation: bool = False

    @property
    def resolved_work_dir(self) -> Path:
        return self.work_dir or self.vault_path / ".sift"

    @property
    def resolved_db_url(self) -> str:
        return self.db_url or f"sqlite:///{self.resolved_work_dir / 'sift.db'}"


def load_settings() -> "Settings":
    return Settings()  # type: ignore[call-arg]
