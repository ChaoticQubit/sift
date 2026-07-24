"""Runtime configuration (env-driven). DESIGN.md §12; CLAUDE.md rule 8."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

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
    # Point at any OpenAI-compatible /v1 to embed fully locally, e.g. Ollama:
    # SIFT_EMBED_BASE_URL=http://localhost:11434/v1 SIFT_EMBED_MODEL=nomic-embed-text
    embed_base_url: str | None = None
    embed_dimensions: int | None = None  # None = the client's default (1536 for OpenAI)

    # Image OCR backend (DESIGN §17): "vlm" = provider vision model via Agno (default,
    # production quality on stylized content); "local" = an OpenAI-compatible local
    # server, e.g. Ollama running Baidu's Unlimited-OCR — free unlimited calls and
    # image bytes never leave the machine (testing default; decided 2026-07-24).
    ocr_backend: Literal["vlm", "local"] = "vlm"
    ocr_base_url: str = "http://localhost:11434/v1"  # Ollama's OpenAI-compat endpoint
    ocr_model: str = "frob/unlimited-ocr:q8_0"

    # Reels/video STT (F4, [stt] extra). Local faster-whisper model name; translation
    # only ever targets English (Whisper's only direction, DESIGN §7) and is opt-in.
    stt_model: str = "small"
    stt_translate_to_english: bool = False

    # Ingestion. Cadence is enforced in code (cookie_fetch refuses runs <20h apart, F2),
    # not by a config string. No instagrapi flag — banned outright (CLAUDE.md rule 1).
    cookie_browser: str = "chrome"  # gallery-dl --cookies-from-browser
    # Your own Instagram username: cookie_fetch targets
    # https://www.instagram.com/<ig_username>/saved/ (required for that source only).
    ig_username: str | None = None

    @property
    def resolved_work_dir(self) -> Path:
        return self.work_dir or self.vault_path / ".sift"


def load_settings() -> "Settings":
    return Settings()  # type: ignore[call-arg]
