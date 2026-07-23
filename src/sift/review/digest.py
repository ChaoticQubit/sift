"""Write / refresh Action Review.md — the human approval surface. DESIGN.md §9.

NEVER fire alerts autonomously (CLAUDE.md rule 7). Items stay status: pending until confirmed.
"""
from __future__ import annotations

from ..config import Settings


def refresh(settings: Settings) -> str:
    """TODO F6: render candidates grouped by due/confidence with links back to their notes."""
    raise NotImplementedError("F6: action-review digest")
