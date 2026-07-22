"""Agno Db (SQLite) for agent sessions + user memory + dedup ledger. DESIGN.md §5, §12."""
from __future__ import annotations

from ..config import Settings


def get_db(settings: Settings):
    """TODO F0: return an Agno Db bound to settings.db_url (SQLite). Shared by reader + enricher."""
    raise NotImplementedError("F0: Agno Db")


def is_new(item_id: str, settings: Settings) -> bool:
    """TODO F0: dedup ledger check (stable Item.id)."""
    raise NotImplementedError("F0: dedup ledger")
