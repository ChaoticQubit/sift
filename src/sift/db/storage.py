"""Dedup ledger (SQLite) — the ONLY durable state in v1. DESIGN.md §12; AGENTS.md §1.

The interpreter is stateless per item (no Agno Db sessions/memory until a genuinely
conversational agent exists — Life Planner fold-in).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

from ..config import Settings


def _connect(settings: Settings) -> sqlite3.Connection:
    work_dir = settings.resolved_work_dir
    work_dir.mkdir(parents=True, exist_ok=True)  # lazy: first ledger touch creates it
    conn = sqlite3.connect(work_dir / "ledger.db")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen ("
        "  item_id TEXT PRIMARY KEY,"
        "  seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    return conn


def is_new(item_id: str, settings: Settings) -> bool:
    """True if this stable Item.id has never been processed into the vault."""
    with closing(_connect(settings)) as conn:
        row = conn.execute(
            "SELECT 1 FROM seen WHERE item_id = ?", (item_id,)
        ).fetchone()
        return row is None


def mark_seen(item_id: str, settings: Settings) -> None:
    """Record an item as processed (called by the writer after a successful note write)."""
    with closing(_connect(settings)) as conn, conn:
        conn.execute("INSERT OR IGNORE INTO seen (item_id) VALUES (?)", (item_id,))
