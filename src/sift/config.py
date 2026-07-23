"""Runtime configuration (env-driven). DESIGN.md §12; CLAUDE.md rule 8."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SIFT_", env_file=".env", extra="ignore"
    )

    vault_path: Path
    # State (dedup ledger + LanceDB) lives WITH the data: <vault>/.sift by default.
    work_dir: Path | None = None

    # Models (provider-swappable; can be local)
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    embed_provider: str = "openai"
    embed_model: str = "text-embedding-3-small"

    # Ingestion. Cadence is enforced in code (cookie_fetch refuses runs <20h apart, F2),
    # not by a config string. No instagrapi flag — banned outright (CLAUDE.md rule 1).
    cookie_browser: str = "chrome"  # gallery-dl --cookies-from-browser

    @property
    def resolved_work_dir(self) -> Path:
        return self.work_dir or self.vault_path / ".sift"


def load_settings() -> "Settings":
    return Settings()  # type: ignore[call-arg]
