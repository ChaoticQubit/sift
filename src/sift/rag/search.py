"""Deterministic search over the Agno Knowledge base — no agent, no model call. DESIGN.md §10.

Keeps the interpreter the only agent in the system and closes the delayed-injection
re-entry path (a model re-reading verbatim vault captions would bypass ingest
guardrails). The only model touchpoint is embedding the QUERY string the user typed —
never stored content coming back through a model.
"""

from __future__ import annotations

from ..config import Settings
from . import knowledge

_MAX_RESULTS = 10
_SNIPPET_CHARS = 200


def query(q: str, settings: Settings) -> list[str]:
    """Direct LanceDB hybrid query -> ranked "path — permalink / snippet" lines."""
    results = knowledge._knowledge(settings).search(query=q, max_results=_MAX_RESULTS)
    lines: list[str] = []
    for doc in results:
        meta = getattr(doc, "meta_data", None) or {}
        snippet = " ".join(str(getattr(doc, "content", "")).split())[:_SNIPPET_CHARS]
        head = meta.get("path", getattr(doc, "name", "?"))
        if meta.get("permalink"):
            head += f" — {meta['permalink']}"
        lines.append(f"{head}\n  {snippet}")
    return lines
