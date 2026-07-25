"""Chunked authenticated media backfill for deferred Instagram items. DESIGN.md §4.

Bulk imports (DYI export) carry NO media bytes — only permalinks. With
SIFT_MEDIA_DEFER on, core.run queues every Instagram-permalink item instead of
interpreting it caption-only; this stage drains that queue in even, schedulable
chunks: download the real media per permalink, then run the ONE extract ->
interpret -> write pass per item WITH its media (OCR/STT included) — one model
call per item, never two.

Download and analysis are PIPELINED: gallery-dl runs in a background thread
while the main thread analyzes each post as soon as it has fully landed
(settle heuristic in _want), so chunk wall time is max(download, analysis)
rather than their sum. Downloads themselves stay strictly serial — one paced
gallery-dl session, per the safety envelope below.

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
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import Settings
from ..guardrails.egress import is_instagram_post_url
from ..models import Item, RunReport
from . import cookie_fetch
from .dyi_export import _item_id

logger = logging.getLogger(__name__)

# A deleted/private post never yields media; after this many chunk-run misses the
# item is written caption-only (status 'nomedia') instead of clogging the queue.
_MAX_ATTEMPTS = 3
_LAST_ATTEMPT_FILE = "media-fetch-last-attempt"
# Pipelining cadence: how often the main thread looks for newly landed posts.
_POLL_SECONDS = 10.0
# A post is safe to analyze mid-download once nothing of it changed for this
# long (carousel files arrive 6-12s apart) and no sibling .part is in flight.
_SETTLE_SECONDS = 60.0


def _want(chunk_ids: set[str], processed: set[str], final: bool, fetch_dir: Path):
    """Sidecar filter for cookie_fetch._items_from_sidecars: never re-parse an
    already-analyzed post; mid-run, pass only THIS chunk's posts that look fully
    downloaded (the fetch dir keeps prior chunks' sidecars — rescanning those
    every poll would re-copy their media for nothing).

    ponytail: settle heuristic (quiet window + no matching .part file). A post
    it wrongly holds back just waits for the final sweep — never lost."""

    def select(canon: str, files: list[Path]) -> bool:
        item_id = _item_id(canon, "")
        if item_id in processed:
            return False
        if final:
            return True
        if item_id not in chunk_ids:
            return False
        stamps = [f.stat().st_mtime for f in files if f.exists()]
        if not stamps or time.time() - max(stamps) < _SETTLE_SECONDS:
            return False
        prefix = files[0].name.split("_")[0]  # "<postid>_<mediaid>.<ext>"
        return not any(p.name.startswith(prefix) for p in fetch_dir.glob("*.part"))

    return select


def _clean_staging(got: Item, fetch_dir: Path) -> None:
    """A processed post's downloads are spent analysis input: reclaim the disk
    (media, metadata sidecars, split DASH streams). The gallery-dl download
    archive still remembers the post, so nothing ever re-downloads. Anything the
    interpreter kept was already copied into the vault by core._dispose_media."""
    for name in got.media_files:
        staged = Path(name)
        if not staged.is_absolute() or fetch_dir not in staged.parents:
            continue
        prefix = re.split(r"[._]", staged.name, maxsplit=1)[0]
        if len(prefix) < 8:  # post ids are long digit runs; never glob a short stem
            continue
        for leftover in fetch_dir.glob(f"{prefix}*"):
            leftover.unlink(missing_ok=True)


def _embed_ready(settings: Settings) -> str | None:
    """Probe the embedder before committing a whole chunk — agno swallows
    per-call embed failures, which yields a silently unlinked vault (chunk-1
    postmortem). Returns the blocker or None; a [rag]-less install skips the
    check (degraded by choice, not by accident)."""
    try:
        from ..rag import knowledge
    except ImportError:
        return None
    return knowledge.embed_probe(settings)


def _refuse(report: RunReport, msg: str) -> None:
    """Refuse loudly: RunReport-visible AND raised (never a silent empty result)."""
    logger.error("media_fetch: %s", msg)
    report.errors.append(f"media_fetch: {msg}")
    raise RuntimeError(f"media_fetch: {msg}")


def requeue_videos(settings: Settings) -> dict[str, int]:
    """Re-open every analyzed reel/video for a fresh pass (after an extraction
    upgrade like frame OCR). Two durable flips, then normal chunks do the rest:
    the queue row goes back to pending, and the gallery-dl download archive
    forgets the post so its media actually re-downloads. The note refreshes in
    place (core's self-refresh guard keeps it from merging into itself)."""
    from ..db import storage

    rows = storage.media_done_videos(settings)
    archive = settings.resolved_work_dir / "gallery-dl-archive.db"
    purged = 0
    if rows and archive.exists():
        with closing(sqlite3.connect(archive)) as conn, conn:
            for _item_id, permalink in rows:
                if m := re.search(r"[?&]id=(\d+)", permalink):
                    purged += conn.execute(
                        "DELETE FROM archive WHERE entry = ?",
                        (f"instagram{m.group(1)}",),
                    ).rowcount
    storage.media_requeue([r[0] for r in rows], settings)
    logger.info(
        "media_fetch: requeued %d video post(s), purged %d archive entrie(s)",
        len(rows),
        purged,
    )
    return {"requeued": len(rows), "archive_purged": purged}


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
        if shutil.which("ffmpeg") is None:
            # Reels arrive as separate DASH video+audio streams; without ffmpeg
            # they never merge into the file the sidecar names, so every reel in
            # the chunk would silently burn an attempt toward caption-only.
            _refuse(
                report,
                "ffmpeg is not installed; reel video+audio streams cannot be "
                "merged. Install it (`brew install ffmpeg`) and rerun.",
            )
        if err := _embed_ready(settings):
            _refuse(
                report,
                f"embedding backend is down ({err}); the whole chunk would be "
                "analyzed without dedup or graph links. Fix the embedder (is "
                "Ollama running?) and rerun.",
            )

        chunk = storage.media_chunk(settings, settings.media_chunk_size)
        if not chunk:
            logger.info("media_fetch: queue empty — media backfill complete")
            return report

        fetch_dir = work / "fetch"
        fetch_dir.mkdir(parents=True, exist_ok=True)
        (work / _LAST_ATTEMPT_FILE).write_text(now.isoformat())
        # Only post-shaped URLs are fetchable; anything else in the queue (e.g. a
        # shared profile link) is closed out caption-only in the loop below.
        post_urls = [row[1] for row in chunk if is_instagram_post_url(row[1])]
        logger.info("media_fetch: fetching chunk of %d permalink(s)", len(post_urls))
        rag = core._rag(settings)
        seen = core._seen(settings)
        chunk_ids = {row[0] for row in chunk}
        processed: set[str] = set()

        def _analyze(item_id: str, queued: Item, got: Item) -> None:
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
            processed.add(item_id)
            _clean_staging(got, fetch_dir)

        def _sweep(final: bool) -> dict[str, Item]:
            # Interim parse noise (a half-written sidecar retries next poll)
            # goes to a scratch report; the final sweep records problems once.
            fetched = {
                i.id: i
                for i in cookie_fetch._items_from_sidecars(
                    fetch_dir,
                    settings,
                    report if final else RunReport(),
                    select=_want(chunk_ids, processed, final, fetch_dir),
                )
            }
            if not final:
                for item_id, permalink, payload, _attempts in chunk:
                    got = fetched.get(item_id)
                    if (
                        item_id not in processed
                        and is_instagram_post_url(permalink)
                        and got is not None
                        and got.media_files
                    ):
                        report.processed += 1
                        _analyze(item_id, Item.model_validate_json(payload), got)
            return fetched

        proc = subprocess.CompletedProcess([], 0, "", "")
        if post_urls:
            argv = cookie_fetch.build_argv(settings, post_urls)
            box: dict = {}

            def _download() -> None:
                try:
                    box["proc"] = cookie_fetch._invoke(argv)
                except BaseException as exc:  # re-raised on the main thread
                    box["exc"] = exc

            # Downloads run in a thread; analysis of already-landed posts runs
            # HERE on the main thread while the rest of the chunk downloads —
            # wall time is max(download, analysis), not their sum.
            thread = threading.Thread(target=_download, name="gallery-dl", daemon=True)
            thread.start()
            sidecars = -1
            polls = 0
            while thread.is_alive():
                thread.join(timeout=_POLL_SECONDS)
                if not thread.is_alive():
                    break
                polls += 1
                count = sum(1 for _ in fetch_dir.rglob("*.json"))
                if count != sidecars or polls % 6 == 0:  # new posts, or catch-up
                    sidecars = count
                    _sweep(final=False)
            exc = box.get("exc")
            if isinstance(exc, OSError):
                _refuse(report, f"could not launch gallery-dl ({exc}); nothing fetched")
            elif isinstance(exc, subprocess.TimeoutExpired):
                proc = subprocess.CompletedProcess(
                    exc.cmd,
                    1,
                    stdout=str(exc.stdout or ""),
                    stderr="gallery-dl hung and was killed after "
                    f"{cookie_fetch._SUBPROCESS_TIMEOUT}s",
                )
            elif exc is not None:
                raise exc  # unexpected: surface loudly (lock released in finally)
            else:
                proc = box["proc"]
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

        fetched = _sweep(final=True)
        for item_id, permalink, payload, attempts in chunk:
            if item_id in processed:
                continue  # analyzed mid-download by the pipeline
            report.processed += 1
            queued = Item.model_validate_json(payload)
            if not is_instagram_post_url(permalink):
                # Defense in depth: only post-shaped URLs are fetchable (a profile
                # URL would pull the whole feed). Write its caption note and close
                # the row out.
                core.process(queued, settings, report, rag=rag, seen=seen)
                storage.media_mark(
                    item_id,
                    "nomedia",
                    "not a post permalink; caption-only note",
                    settings,
                )
                continue
            got = fetched.get(item_id)
            if got is not None and got.media_files:
                _analyze(item_id, queued, got)
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
