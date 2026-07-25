"""Durable state (SQLite): the dedup ledger + the media backfill queue. DESIGN.md §12.

The interpreter is stateless per item (no Agno Db sessions/memory until a genuinely
conversational agent exists — Life Planner fold-in).

media_queue holds Instagram-permalink items deferred by core.run (media_defer) until
the chunked backfill fetches their real media. Manually auditable at any time:
    sqlite3 <work_dir>/ledger.db "SELECT status, COUNT(*) FROM media_queue GROUP BY status"
Statuses: pending (awaiting a chunk) | done (media fetched, note written) |
nomedia (fetch exhausted — deleted/private post — note written caption-only).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

from ..config import Settings
from ..models import Item


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
    conn.execute(
        "CREATE TABLE IF NOT EXISTS media_queue ("
        "  item_id TEXT PRIMARY KEY,"
        "  permalink TEXT NOT NULL,"
        "  payload TEXT NOT NULL,"  # full Item JSON — the chunk run rebuilds it
        "  status TEXT NOT NULL DEFAULT 'pending',"
        "  attempts INTEGER NOT NULL DEFAULT 0,"
        "  detail TEXT NOT NULL DEFAULT '',"
        "  enqueued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  updated_at TEXT"
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


# --------------------------------------------------------- media backfill queue


def enqueue_media(item: Item, settings: Settings) -> bool:
    """Queue a deferred item (full Item JSON) for the chunked media backfill.
    Idempotent: an already-queued id is left untouched. True = newly queued."""
    with closing(_connect(settings)) as conn, conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO media_queue (item_id, permalink, payload)"
            " VALUES (?, ?, ?)",
            (item.id, item.permalink or "", item.model_dump_json()),
        )
        return cur.rowcount > 0


def media_chunk(settings: Settings, limit: int) -> list[tuple[str, str, str, int]]:
    """The next even chunk: oldest pending rows first.
    Returns (item_id, permalink, payload, attempts) tuples."""
    with closing(_connect(settings)) as conn:
        return conn.execute(
            "SELECT item_id, permalink, payload, attempts FROM media_queue"
            " WHERE status = 'pending' ORDER BY enqueued_at, item_id LIMIT ?",
            (limit,),
        ).fetchall()


def media_mark(item_id: str, status: str, detail: str, settings: Settings) -> None:
    """Terminal outcome for a queued item: 'done' or 'nomedia' (+ human detail)."""
    with closing(_connect(settings)) as conn, conn:
        conn.execute(
            "UPDATE media_queue SET status = ?, detail = ?,"
            " updated_at = CURRENT_TIMESTAMP WHERE item_id = ?",
            (status, detail, item_id),
        )


def media_bump(item_id: str, settings: Settings) -> None:
    """Count a fetch miss; the item stays pending for the next chunk."""
    with closing(_connect(settings)) as conn, conn:
        conn.execute(
            "UPDATE media_queue SET attempts = attempts + 1,"
            " updated_at = CURRENT_TIMESTAMP WHERE item_id = ?",
            (item_id,),
        )


def media_done_videos(settings: Settings) -> list[tuple[str, str]]:
    """(item_id, permalink) of DONE rows whose queued payload is a reel/video —
    the reanalysis set after an extraction upgrade (e.g. frame OCR)."""
    with closing(_connect(settings)) as conn:
        return conn.execute(
            "SELECT item_id, permalink FROM media_queue WHERE status = 'done'"
            " AND json_extract(payload, '$.medium') IN ('reel', 'video')"
        ).fetchall()


def media_requeue(item_ids: list[str], settings: Settings) -> None:
    """Re-open rows for a fresh fetch+analysis pass: pending again, attempts reset."""
    with closing(_connect(settings)) as conn, conn:
        conn.executemany(
            "UPDATE media_queue SET status = 'pending', attempts = 0, detail = '',"
            " updated_at = CURRENT_TIMESTAMP WHERE item_id = ?",
            [(i,) for i in item_ids],
        )


def media_counts(settings: Settings) -> dict[str, int]:
    """status -> count, for reports and manual auditing."""
    with closing(_connect(settings)) as conn:
        return dict(
            conn.execute(
                "SELECT status, COUNT(*) FROM media_queue GROUP BY status"
            ).fetchall()
        )
