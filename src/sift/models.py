"""Core data contracts. Keep stable — the fold-in seam (CLAUDE.md Conventions)."""
from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel


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
    media_files: list[str] = []
    raw: dict = {}


class Extraction(BaseModel):
    """Uniform output of every extractor (downstream stays modality-agnostic)."""

    text: str = ""
    languages: list[str] = []
    confidence: float | None = None
    artifacts: dict = {}


class ActionItem(BaseModel):
    text: str
    due: date | None = None
    confidence: float = 0.0
    status: str = "pending"  # pending | confirmed | dismissed


class Interpretation(BaseModel):
    """The interpreter agent's ONLY output — strict schema, validated before any code acts on it.

    This is the trust boundary between the quarantined model and privileged code (AGENTS.md §2).
    """

    classification: str = "reference"  # educational | reference | entertainment
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
