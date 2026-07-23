"""Deterministic search over the Agno Knowledge base — no agent, no model call. DESIGN.md §10.

Keeps the interpreter the only model in the system and closes the delayed-injection
re-entry path (a model re-reading verbatim vault captions would bypass ingest guardrails).
"""

from __future__ import annotations

from ..config import Settings


def query(q: str, settings: Settings) -> list[str]:
    """TODO F5: direct LanceDB hybrid query -> ranked notes + snippets + permalinks."""
    raise NotImplementedError("F5: deterministic hybrid search")
