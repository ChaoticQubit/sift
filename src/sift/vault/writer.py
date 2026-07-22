"""Idempotent markdown note writer. DESIGN.md §6; CLAUDE.md rule 3."""
from __future__ import annotations

from ..config import Settings
from ..models import Note


def write(note: Note, settings: Settings) -> str:
    """Write / refresh a capture note in <vault>/Captures. Idempotent by Item.id.

    entertainment -> one-line log entry only. Returns the note path.
    TODO F0: python-frontmatter dump; media embed; dedup ledger update in .sift/.
    """
    raise NotImplementedError("F0: idempotent note write")
