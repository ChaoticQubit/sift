"""Core data contracts. Keep stable — the fold-in seam (CLAUDE.md Conventions)."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Medium(str, Enum):
    reel = "reel"
    carousel = "carousel"
    image = "image"
    video = "video"
    link = "link"
    text = "text"


class IngestVia(str, Enum):
    cookie_fetch = "cookie_fetch"
    share_capture = "share_capture"
    dyi_export = "dyi_export"


class Item(BaseModel):
    """A saved / DM'd / shared thing, normalized by an ingest adapter (pre-extraction)."""

    id: str  # stable: hash(permalink) + saved_at
    permalink: str | None = None
    medium: Medium
    caption: str = ""  # FIRST-CLASS content (DESIGN.md §7) — never drop it
    ingest_via: IngestVia
    saved_collection: str | None = None
    saved_at: date | None = None
    source_export: str | None = None  # e.g. "instagram-2026-07-20" (dyi_export only)
    media_files: list[str] = []
    raw: dict = {}


class Extraction(BaseModel):
    """Uniform output of every extractor (downstream stays modality-agnostic)."""

    text: str = ""
    languages: list[str] = []
    confidence: float | None = None
    artifacts: dict = {}


class Classification(str, Enum):
    educational = "educational"
    reference = "reference"
    entertainment = "entertainment"


class ActionItem(BaseModel):
    text: str
    due: date | None = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    status: Literal["pending", "confirmed", "dismissed"] = "pending"

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v: object) -> object:
        """Clamp, not reject (AGENTS.md §2). Runs before ge/le, so an out-of-range model
        value is coerced into [0,1] here — including inside agno's own output_schema
        parse, which would otherwise swallow the ValidationError and degrade the whole
        Interpretation to a raw string (reject, not clamp)."""
        try:
            return min(1.0, max(0.0, float(v)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return v  # non-numeric: let pydantic report it


class Interpretation(BaseModel):
    """The interpreter agent's ONLY output — strict schema, validated before any code acts on it.

    This is the trust boundary between the quarantined model and privileged code (AGENTS.md §2).
    Schema validates SHAPE, not honesty: boundary code still re-forces status to "pending" on
    every model-emitted action item and treats all values as proposals.
    """

    classification: Classification = Classification.reference
    summary: str = ""
    tags: list[str] = []
    action_items: list[ActionItem] = []


class Note(BaseModel):
    item: Item
    extraction: Extraction
    interpretation: Interpretation = Interpretation()


class RunReport(BaseModel):
    processed: int = 0
    written: int = 0
    skipped: int = 0
    media_missing: int = 0
    guardrail_blocks: int = 0
    notes: list[str] = []
    errors: list[str] = []
