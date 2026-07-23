"""Dedup ledger (SQLite) — the ONLY durable state in v1. DESIGN.md §12; AGENTS.md §1.

The interpreter is stateless per item (no Agno Db sessions/memory until a genuinely
conversational agent exists — Life Planner fold-in).
"""
from __future__ import annotations

from ..config import Settings


def is_new(item_id: str, settings: Settings) -> bool:
    """TODO F0: check the seen-items ledger in settings.resolved_work_dir (stable Item.id)."""
    raise NotImplementedError("F0: dedup ledger")


def mark_seen(item_id: str, settings: Settings) -> None:
    """TODO F0: record an item as processed (called by the writer after a successful note write)."""
    raise NotImplementedError("F0: dedup ledger")
