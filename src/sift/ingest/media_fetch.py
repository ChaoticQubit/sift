"""Chunked authenticated media backfill for deferred Instagram items. DESIGN.md §4.

Bulk imports (DYI export) carry NO media bytes — only permalinks. With
SIFT_MEDIA_DEFER on, core.run queues every Instagram-permalink item instead of
interpreting it caption-only; this stage drains that queue in even, schedulable
chunks: download the real media per permalink, then run the ONE extract ->
interpret -> write pass per item WITH its media (OCR/STT included) — one model
call per item, never two.

Progress is durable and manually auditable in the media_queue table of
<work_dir>/ledger.db (db/storage.py):
    sqlite3 <work_dir>/ledger.db "SELECT status, COUNT(*) FROM media_queue GROUP BY status"
pending = awaiting a chunk; done = media fetched + note written; nomedia = fetch
exhausted after _MAX_ATTEMPTS (deleted/private post) + note written caption-only —
nothing is ever silently dropped.

Safety shares cookie_fetch's envelope (same account, same session rules):
  - the SAME single-session lock (one authenticated Instagram session at a time
    across both fetchers);
  - the SAME 429 back-off pause file — a rate limit in either fetcher pauses both,
    and a clean run here resets the shared ladder;
  - a 4h attempt floor (no 20h success gate: chunks are MEANT to run daily; the
    floor is what stops a misconfigured hourly job from hammering);
  - every permalink routed through the egress gate inside build_argv;
  - one pinned --config-ignore gallery-dl invocation per chunk (argv list, never
    a shell), the shared download archive, and the extractor-default 6-12s
    sleep-request pacing between requests.
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone

from ..config import Settings
from ..models import Item, RunReport
from . import cookie_fetch

logger = logging.getLogger(__name__)

# A deleted/private post never yields media; after this many chunk-run misses the
# item is written caption-only (status 'nomedia') instead of clogging the queue.
_MAX_ATTEMPTS = 3
_LAST_ATTEMPT_FILE = "media-fetch-last-attempt"


def _refuse(report: RunReport, msg: str) -> None:
    """Refuse loudly: RunReport-visible AND raised (never a silent empty result)."""
    logger.error("media_fetch: %s", msg)
    report.errors.append(f"media_fetch: {msg}")
    raise RuntimeError(f"media_fetch: {msg}")


def run(settings: Settings) -> RunReport:
    """Fetch + fully analyze the next even chunk of queued items.

    Guards run FIRST and refuse loudly (shared 429 pause, 4h attempt floor, shared
    session lock). A rate-limited chunk still processes whatever was downloaded and
    leaves unreached items pending WITHOUT burning their attempts.
    """
    from .. import core
    from ..db import storage

    report = RunReport()
    work = settings.resolved_work_dir
    work.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    # SAME lock file as cookie_fetch: one authenticated Instagram session, total.
    lock = (work / cookie_fetch._LOCK_FILE).open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        _refuse(
            report,
            "another Instagram fetch is already in progress (lock held); refusing "
            "a second parallel session (DESIGN.md §4)",
        )
    try:
        pause_until, pause_days = cookie_fetch._read_pause(work)
        if msg := cookie_fetch.backoff_refusal(pause_until, now):
            _refuse(report, msg)
        last = cookie_fetch._read_stamp(work, _LAST_ATTEMPT_FILE)
        floor = timedelta(hours=cookie_fetch._MIN_ATTEMPT_GAP_HOURS)
        if last is not None and now - last < floor:
            _refuse(
                report,
                f"last media chunk ran {(now - last).total_seconds() / 3600:.1f}h "
                f"ago; refusing another within "
                f"{cookie_fetch._MIN_ATTEMPT_GAP_HOURS}h — chunks are daily-paced, "
                f"never hourly (DESIGN.md §4). Try after "
                f"{last + floor:%Y-%m-%d %H:%M} UTC.",
            )
        if importlib.util.find_spec("gallery_dl") is None:
            _refuse(
                report,
                "gallery_dl is not importable in this Python environment; reinstall "
                "Sift's deps (`pip install -e .`)",
            )

        chunk = storage.media_chunk(settings, settings.media_chunk_size)
        if not chunk:
            logger.info("media_fetch: queue empty — media backfill complete")
            return report

        fetch_dir = work / "fetch"
        fetch_dir.mkdir(parents=True, exist_ok=True)
        (work / _LAST_ATTEMPT_FILE).write_text(now.isoformat())
        logger.info("media_fetch: fetching chunk of %d permalink(s)", len(chunk))
        try:
            proc = cookie_fetch._invoke(
                cookie_fetch.build_argv(settings, [row[1] for row in chunk])
            )
        except OSError as exc:
            _refuse(report, f"could not launch gallery-dl ({exc}); nothing fetched")
        except subprocess.TimeoutExpired as exc:
            proc = subprocess.CompletedProcess(
                exc.cmd,
                1,
                stdout=str(exc.stdout or ""),
                stderr="gallery-dl hung and was killed after "
                f"{cookie_fetch._SUBPROCESS_TIMEOUT}s",
            )
        output = f"{proc.stdout}\n{proc.stderr}"

        rate_limited = bool(cookie_fetch._RATE_LIMIT.search(output))
        if rate_limited:
            days = cookie_fetch.grow_backoff(pause_days)
            resume = now + timedelta(days=days)
            (work / cookie_fetch._PAUSE_FILE).write_text(
                json.dumps({"until": resume.isoformat(), "days": days})
            )
            msg = (
                f"media_fetch: Instagram rate-limited this chunk (HTTP 429); pausing "
                f"BOTH fetchers for {days} day(s), until {resume:%Y-%m-%d %H:%M} "
                "UTC. Items already downloaded are still processed; the rest stay "
                "pending."
            )
            logger.warning(msg)
            report.errors.append(msg)
        elif proc.returncode != 0:
            lines = (proc.stderr or proc.stdout).strip().splitlines()
            msg = (
                f"media_fetch: gallery-dl exited {proc.returncode}"
                f" ({lines[-1] if lines else 'no output'}); check cookies "
                f"(--cookies-from-browser {settings.cookie_browser}). Items already "
                "downloaded are still processed."
            )
            logger.warning(msg)
            report.errors.append(msg)
        else:
            # Clean chunk resets the SHARED back-off ladder, like cookie_fetch.
            (work / cookie_fetch._PAUSE_FILE).unlink(missing_ok=True)

        fetched = {
            i.id: i
            for i in cookie_fetch._items_from_sidecars(fetch_dir, settings, report)
        }
        rag = core._rag(settings)
        seen = core._seen(settings)
        for item_id, _permalink, payload, attempts in chunk:
            report.processed += 1
            queued = Item.model_validate_json(payload)
            got = fetched.get(item_id)
            if got is not None and got.media_files:
                # The sidecar knows media truth (files, carousel/reel medium); the
                # queued payload keeps import provenance (collection, saved date,
                # export name, original caption).
                merged = queued.model_copy(
                    update={
                        "media_files": got.media_files,
                        "medium": got.medium,
                        "caption": queued.caption or got.caption,
                    }
                )
                core.process(merged, settings, report, rag=rag, seen=seen)
                storage.media_mark(
                    item_id, "done", f"{len(got.media_files)} media file(s)", settings
                )
            elif rate_limited:
                pass  # not the post's fault — retry next chunk, attempts unchanged
            elif attempts + 1 >= _MAX_ATTEMPTS:
                # caption-only, still ONE pass
                core.process(queued, settings, report, rag=rag, seen=seen)
                storage.media_mark(
                    item_id,
                    "nomedia",
                    f"no media after {attempts + 1} attempts (deleted/private?); "
                    "caption-only note written",
                    settings,
                )
            else:
                storage.media_bump(item_id, settings)

        core.finalize(report, settings, rag=rag)
        logger.info("media_fetch: queue now %s", storage.media_counts(settings))
        return report
    finally:
        lock.close()  # releases the flock
