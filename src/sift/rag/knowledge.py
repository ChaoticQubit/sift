"""Agno Knowledge base over LanceDB (agentic RAG, hybrid search). DESIGN.md §10."""
from __future__ import annotations

from ..config import Settings


def build(settings: Settings, rebuild: bool = False) -> None:
    """TODO F5: chunk + embed notes into an Agno Knowledge/LanceDB store; incremental unless rebuild."""
    raise NotImplementedError("F5: build knowledge base")
