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
    dm_fetch = "dm_fetch"


class Item(BaseModel):
    """A saved / DM'd / shared thing, normalized by an ingest adapter (pre-extraction)."""

    id: str  # stable: sha256(canonical permalink) — date-free so export+fetch dedup; text notes hash text+timestamp
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


class Domain(str, Enum):
    """The FIXED human-scale shelf set (decided 2026-08-07): a normal reader can hold
    ~10 domains in their head, so Topics/ nests thousands of model-emitted tags under
    these instead of one flat hub per tag. Specific tags stay in note frontmatter for
    search/RAG — the model side never needed the hierarchy, the human side does."""

    technology = "technology"
    career = "career"
    education = "education"
    finance = "finance"
    entertainment = "entertainment"
    travel = "travel"
    food = "food"
    health = "health"
    lifestyle = "lifestyle"
    inspiration = "inspiration"
    misc = "misc"


class ActionItem(BaseModel):
    text: str
    due: date | None = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    status: Literal["pending", "confirmed", "dismissed"] = "pending"
    priority: Literal["high", "medium", "low"] | None = None

    @field_validator("priority", mode="before")
    @classmethod
    def _coerce_priority(cls, v: object) -> object:
        """Coerce, not reject: an off-vocabulary model value ("urgent") degrades to
        None (display falls back to confidence), never fails the Interpretation."""
        s = str(v).strip().lower() if v is not None else ""
        return s if s in ("high", "medium", "low") else None

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


class Resource(BaseModel):
    """One concrete named thing a post mentions — a book, course, tool, repo, person."""

    name: str
    kind: str = ""  # book | course | tool | repo | article | person | other
    detail: str = ""


class Interpretation(BaseModel):
    """The interpreter agent's ONLY output — strict schema, validated before any code acts on it.

    This is the trust boundary between the quarantined model and privileged code (AGENTS.md §2).
    Schema validates SHAPE, not honesty: boundary code still re-forces status to "pending" on
    every model-emitted action item and treats all values as proposals. keep_media is only a
    proposal too: core honors it solely for staged (downloaded) images, never videos.
    """

    classification: Classification = Classification.reference
    domain: Domain = Domain.misc
    summary: str = ""
    key_points: list[str] = []  # the post's actual substance, one point per entry
    resources: list[Resource] = []
    assessment: str = ""  # why it's worth (or not worth) keeping; what to do with it
    keep_media: bool = False  # image is a dense visual reference worth reopening
    tags: list[str] = []
    action_items: list[ActionItem] = []

    @field_validator("domain", mode="before")
    @classmethod
    def _coerce_domain(cls, v: object) -> object:
        """Coerce, not reject (same reasoning as ActionItem confidence): an off-enum
        domain ("tech") must degrade to misc, never fail the whole Interpretation."""
        values = {d.value for d in Domain}
        s = str(v).strip().lower() if v is not None else ""
        return s if s in values else Domain.misc


class Note(BaseModel):
    item: Item
    extraction: Extraction
    interpretation: Interpretation = Interpretation()


class RunReport(BaseModel):
    processed: int = 0
    written: int = 0
    skipped: int = 0
    deferred: int = 0  # queued for the chunked media backfill (ingest/media_fetch)
    merged: int = 0  # near-duplicates folded into an existing page (rag/link.py)
    media_missing: int = 0
    guardrail_blocks: int = 0
    notes: list[str] = []
    errors: list[str] = []
