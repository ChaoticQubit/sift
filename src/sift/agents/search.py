"""Search agent over the Agno Knowledge base (agentic RAG). DESIGN.md §10. AGENTS.md §1."""
from __future__ import annotations

from ..config import Settings


def query(q: str, settings: Settings) -> list[str]:
    """TODO F5: agentic retrieval over LanceDB knowledge -> ranked notes + snippets + permalinks."""
    raise NotImplementedError("F5: search agent")
