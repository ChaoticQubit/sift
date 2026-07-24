"""Agno Knowledge over LanceDB (hybrid) — deterministic indexing, no agent. DESIGN.md §10; F5.

The EMBED-BOUND copy of note text is PII-masked with the SAME callable as the
interpreter path (AGENTS.md §3a) before it can reach a remote embedder; the vault
note stays verbatim. With SIFT_EMBED_BASE_URL pointed at Ollama the embedder is
fully local and nothing leaves the machine. The index lives in <work_dir>/lancedb
and is derived state — safe to delete, rebuilt from the vault with `sift index`.

Heavy deps ([rag] extra: lancedb + openai client) are imported lazily; core.run
degrades to unindexed notes with a logged install hint when they are absent.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import frontmatter

from ..config import Settings
from ..guardrails.pii import mask_for_model

logger = logging.getLogger(__name__)

_TABLE = "captures"


def _knowledge(settings: Settings):
    """Build the Knowledge handle (lazy [rag] imports). Seam for offline tests."""
    from agno.knowledge.embedder.openai import OpenAIEmbedder
    from agno.knowledge.knowledge import Knowledge
    from agno.vectordb.lancedb import LanceDb, SearchType

    kwargs: dict = {"id": settings.embed_model}
    if settings.embed_dimensions:
        kwargs["dimensions"] = settings.embed_dimensions
    if settings.embed_base_url:
        kwargs["base_url"] = settings.embed_base_url
        # A local OpenAI-compatible server (Ollama) ignores the key but the client
        # requires one; never send the real OpenAI key to a non-OpenAI host.
        kwargs["api_key"] = os.environ.get("SIFT_EMBED_API_KEY", "local")
    return Knowledge(
        name="sift-captures",
        vector_db=LanceDb(
            uri=str(settings.resolved_work_dir / "lancedb"),
            table_name=_TABLE,
            search_type=SearchType.hybrid,
            embedder=OpenAIEmbedder(**kwargs),
            use_tantivy=False,  # lance-native FTS; no extra dep
        ),
    )


def embed_probe(settings: Settings, handle=None) -> str | None:
    """One tiny real embedding call; returns the blocker string, or None when fine.

    agno swallows per-call embed failures (logs them and returns no vectors), so a
    down embedder silently produces an unindexed, unlinked vault (chunk-1
    postmortem: a broken Ollama 500'd every embed and the run still reported zero
    errors). Probe once per run and fail LOUDLY instead."""
    try:
        handle = handle or _knowledge(settings)
        embedder = getattr(getattr(handle, "vector_db", None), "embedder", None)
        if embedder is None:
            return None  # non-standard handle (tests); nothing to probe
        if not embedder.get_embedding("sift embedder probe"):
            return "embedder returned an empty vector"
        return None
    except Exception as exc:
        return str(exc) or type(exc).__name__


def _embed_text(post: frontmatter.Post) -> str:
    """Compose the embed-bound copy: caption + body, PII-masked (vault verbatim)."""
    caption = str(post.metadata.get("caption", ""))
    return mask_for_model(f"{caption}\n\n{post.content}".strip())


def index_note(path: str | Path, settings: Settings, knowledge=None) -> None:
    """Upsert ONE written note into the index (the incremental path core.run uses)."""
    path = Path(path)
    post = frontmatter.load(path)
    (knowledge or _knowledge(settings)).add_content(
        name=path.stem,
        text_content=_embed_text(post),
        metadata={
            "path": str(path),
            "permalink": str(post.metadata.get("source", "")),
            "classification": str(post.metadata.get("classification", "")),
        },
        upsert=True,  # idempotent by name — a refreshed note replaces its row
    )


def build(settings: Settings, rebuild: bool = False) -> int:
    """Sweep <vault>/Captures into the index (backfill / repair). Returns notes indexed."""
    if rebuild:
        import shutil

        shutil.rmtree(settings.resolved_work_dir / "lancedb", ignore_errors=True)
    knowledge = _knowledge(settings)
    count = 0
    for note in sorted((settings.vault_path / "Captures").glob("*.md")):
        index_note(note, settings, knowledge=knowledge)
        count += 1
    logger.info("rag: indexed %d note(s)", count)
    return count
