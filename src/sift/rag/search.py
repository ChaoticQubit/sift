"""Query the Agno Knowledge base. DESIGN.md §10."""
from __future__ import annotations

from ..config import Settings


def query(q: str, settings: Settings) -> list[str]:
    """TODO F5: delegate to agents.search.query (agentic retrieval)."""
    from ..agents import search
    return search.query(q, settings)
