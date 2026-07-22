"""Build / parse the capture-note frontmatter (Life-Planner-compatible). DESIGN.md §6."""
from __future__ import annotations

from ..models import Note


def to_frontmatter(note: Note) -> dict:
    """TODO F0: map Note -> the §6 frontmatter dict (type, source, platform, medium, ...)."""
    raise NotImplementedError("F0: frontmatter builder")
