"""Adapter: weekly local cookie-based fetch of the user's OWN Saved folder. DESIGN.md §4.

RUNS ON THE USER'S MACHINE + HOME IP. READ-ONLY. Never like/follow/comment/DM —
no such code path exists here: gallery-dl is invoked as a plain downloader via an
argv list (never a shell) against its saved-media extractor only, reusing the
user's real logged-in browser session (--cookies-from-browser; no fresh-login flow).

Safety is enforced in code, not prose (trust boundary — exempt from simplification,
CLAUDE.md gate 5):
  - Cadence: refuses to run < 20h after the last SUCCESSFUL run ("weekly default,
    daily ceiling, never hourly" — DESIGN.md §4); the timestamp is recorded only
    after a clean run.
  - 429 back-off: the first rate-limit marker pauses future runs for 1 day,
    doubling on each rate-limited run (2d, 4d, ... capped at 14d); a clean run
    resets the ladder. While paused, runs refuse and say until when.
  - Host scope: every recorded URL and every downloaded file's source host must be
    instagram.com / cdninstagram.com / fbcdn.net (subdomain-aware suffix match —
    guardrails/egress.py); anything else is logged, counted, and skipped.

Verified against the installed gallery-dl 1.32.7 source:
  - Saved extractor: extractor/instagram.py:755-762 `InstagramSavedExtractor`,
    pattern USER_PATTERN + r"/saved(?:/all-posts)?/?$", example
    "https://www.instagram.com/USER/saved/" — exactly the URL built here.
  - Sidecars (--write-metadata): per-file JSON next to each download carrying the
    merged post+file dict — `description` (the caption, instagram.py:246),
    `post_url` (/p/ or /reel/, instagram.py:399-402), `date` ("YYYY-MM-DD HH:MM:SS"
    via util.json_default str()), `display_url`/`video_url`, `type`, `username`.
  - Rate limiting: extractor/common.py:227-251 retries a 429 with
    wait(reason="429 Too Many Requests"); once retries are exhausted it raises
    HttpError "'429 Too Many Requests' for '<url>'" (logged by job.py:176), exit
    status bit 4 (exception.py: ExtractionError.code = 4). Both surfaces contain
    the marker matched by _RATE_LIMIT.
  - --sleep-request "6.0-12.0" is a valid randomized range (util.py:889
    build_selection_func) matching the instagram extractor's own default
    request_interval = (6.0, 12.0) (instagram.py:30) — well above the 3s floor.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from ..config import Settings
from ..guardrails.egress import is_instagram_host
from ..models import IngestVia, Item, Medium, RunReport

# Deliberate sibling reuse (house F1 helpers), not an abstraction layer.
from .dyi_export import (
    _copy_media,
    _fix_mojibake,
    _infer_medium,
    _item_id,
    canonical_permalink,
)

logger = logging.getLogger(__name__)

_MIN_GAP_HOURS = 20  # "weekly default, daily ceiling, never hourly" — DESIGN.md §4
_BACKOFF_CAP_DAYS = 14
_LAST_RUN_FILE = "cookie-fetch-last-run"
_PAUSE_FILE = "cookie-fetch-pause.json"
_SLEEP_REQUEST = "6.0-12.0"  # extractor's own default range; floor is 3s
# The exact strings the installed gallery-dl emits for rate limiting (module docstring).
_RATE_LIMIT = re.compile(r"(?i)429 too many requests|rate limit")
# Instagram's real username charset; also keeps the value inert inside the URL.
_USERNAME = re.compile(r"[A-Za-z0-9._]{1,30}")


# ------------------------------------------------------------------ pure guard logic


def cadence_refusal(last_run: datetime | None, now: datetime) -> str | None:
    """None = allowed; else the actionable refusal message (< 20h since last success)."""
    if last_run is None:
        return None
    elapsed = now - last_run
    if elapsed >= timedelta(hours=_MIN_GAP_HOURS):
        return None
    next_ok = last_run + timedelta(hours=_MIN_GAP_HOURS)
    return (
        f"cookie_fetch last succeeded {elapsed.total_seconds() / 3600:.1f}h ago; "
        f"refusing to fetch again within {_MIN_GAP_HOURS}h (weekly default, daily "
        f"ceiling, never hourly — DESIGN.md §4). "
        f"Try again after {next_ok:%Y-%m-%d %H:%M} UTC."
    )


def backoff_refusal(pause_until: datetime | None, now: datetime) -> str | None:
    """None = allowed; else the refusal message naming when the 429 pause ends."""
    if pause_until is None or now >= pause_until:
        return None
    return (
        f"cookie_fetch is paused until {pause_until:%Y-%m-%d %H:%M} UTC after "
        "Instagram rate-limiting (HTTP 429); wait it out — retrying sooner raises "
        "ban risk (DESIGN.md §4)."
    )


def grow_backoff(previous_days: int | None) -> int:
    """Exponential pause in DAYS: 1 -> 2 -> 4 -> ... capped at 14; reset by a clean run."""
    if not previous_days or previous_days < 1:
        return 1
    return min(previous_days * 2, _BACKOFF_CAP_DAYS)


def build_argv(settings: Settings) -> list[str]:
    """The exact gallery-dl invocation — argv list, NEVER shell=True or interpolation.

    Strictly a read-only download: gallery-dl has no like/follow/comment/DM
    capability at all, and no extractor options (-o) are passed — the argv is
    exactly this list (asserted byte-for-byte in tests)."""
    work = settings.resolved_work_dir
    return [
        sys.executable,
        "-m",
        "gallery_dl",
        "--cookies-from-browser",
        settings.cookie_browser,
        "--sleep-request",
        _SLEEP_REQUEST,
        "--download-archive",
        str(work / "gallery-dl-archive.db"),
        "--write-metadata",
        "--directory",
        str(work / "fetch"),
        f"https://www.instagram.com/{settings.ig_username}/saved/",
    ]


# ----------------------------------------------------------------- state + subprocess


def _read_last_run(work: Path) -> datetime | None:
    try:
        return datetime.fromisoformat((work / _LAST_RUN_FILE).read_text().strip())
    except (OSError, ValueError):
        return None


def _read_pause(work: Path) -> tuple[datetime | None, int | None]:
    """(pause_until, pause_days) from the state file; (None, None) when absent/corrupt."""
    try:
        data = json.loads((work / _PAUSE_FILE).read_text())
        return datetime.fromisoformat(data["until"]), int(data["days"])
    except (OSError, ValueError, TypeError, KeyError):
        return None, None


def _refuse(report: RunReport, msg: str) -> None:
    """Refuse loudly: RunReport-visible AND raised (never a silent empty result)."""
    logger.error("cookie_fetch: %s", msg)
    report.errors.append(f"cookie_fetch: {msg}")
    raise RuntimeError(f"cookie_fetch: {msg}")


def _invoke(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Seam for offline tests. argv list only — shell=True is banned."""
    return subprocess.run(argv, capture_output=True, text=True, check=False)


# --------------------------------------------------------------- sidecar -> Item


def _host_ok(url: str) -> bool:
    host = urlsplit(url).hostname
    return bool(host) and is_instagram_host(host)


def _post_date(meta: dict) -> date | None:
    try:
        return datetime.fromisoformat(str(meta["date"])).date()
    except (KeyError, ValueError):
        return None


def _medium(meta: dict, files: list[Path]) -> Medium:
    if len(files) > 1:
        return Medium.carousel  # one sidecar per file; several files = sidecar post
    if meta.get("video_url"):
        return Medium.reel if meta.get("type") == "reel" else Medium.video
    return _infer_medium(meta["post_url"])  # F1 helper: /reel/, /tv/, else image


def _items_from_sidecars(
    fetch_dir: Path, settings: Settings, report: RunReport
) -> list[Item]:
    """Parse every metadata sidecar under fetch/ into Items, host-checked.

    Old sidecars from previous runs re-parse harmlessly: the dedup ledger skips
    already-written ids in core.run. Nothing is silently dropped — every rejected
    sidecar is logged and counted."""
    counts: Counter = Counter()
    posts: dict[str, dict] = {}  # canonical permalink -> {"meta": ..., "files": [...]}
    for sidecar in sorted(fetch_dir.rglob("*.json")):
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            counts["skipped"] += 1
            msg = f"unreadable metadata sidecar {sidecar.name}; entry skipped"
            logger.warning("cookie_fetch: %s", msg)
            report.errors.append(f"cookie_fetch: {msg}")
            continue
        if not isinstance(meta, dict) or not meta.get("post_url"):
            counts["skipped"] += 1
            msg = f"sidecar {sidecar.name} has no post_url; entry skipped"
            logger.warning("cookie_fetch: %s", msg)
            report.errors.append(f"cookie_fetch: {msg}")
            continue
        # HOST SCOPE (AGENTS.md §3a): the permalink AND the media source hosts.
        bad = [
            u
            for u in (meta["post_url"], meta.get("display_url"), meta.get("video_url"))
            if u and not _host_ok(u)
        ]
        if bad:
            report.guardrail_blocks += 1
            msg = f"non-Instagram host in {sidecar.name} ({bad[0]}); item skipped"
            logger.warning("cookie_fetch: %s", msg)
            report.errors.append(f"cookie_fetch: {msg}")
            continue
        group = posts.setdefault(
            canonical_permalink(meta["post_url"]), {"meta": meta, "files": []}
        )
        media = sidecar.with_suffix("")  # "<file>.jpg.json" -> "<file>.jpg"
        if media.is_file():
            group["files"].append(media)
        else:
            counts["media_missing"] += 1
            logger.warning(
                "cookie_fetch: media for %s not downloaded; permalink-only",
                sidecar.name,
            )

    items: list[Item] = []
    for canon, group in posts.items():
        meta, files = group["meta"], sorted(group["files"])
        items.append(
            Item(
                id=_item_id(canon, ""),  # F1 convention: dedups with dyi_export
                permalink=meta["post_url"],
                medium=_medium(meta, files),
                caption=_fix_mojibake(meta.get("description") or ""),
                ingest_via=IngestVia.cookie_fetch,
                saved_at=_post_date(meta),
                media_files=_copy_media(
                    fetch_dir,
                    [f.relative_to(fetch_dir).as_posix() for f in files],
                    settings,
                    counts,
                    report,
                ),
                raw={"username": meta.get("username", "")},
            )
        )
    report.media_missing += counts["media_missing"]
    logger.info(
        "cookie_fetch: %d items from %d sidecars (%d skipped, %d media missing)",
        len(items),
        sum(len(g["files"]) for g in posts.values()) + counts["skipped"],
        counts["skipped"],
        counts["media_missing"],
    )
    return items


# ------------------------------------------------------------------------------ pull


def pull(
    settings: Settings, path: str | None = None, report: RunReport | None = None
) -> list[Item]:
    """Incremental read-only gallery-dl fetch of the Saved folder -> Items.

    Guards run FIRST (back-off pause, then cadence) and refuse loudly. A rate-limited
    or failed run still processes whatever was already downloaded; only a clean run
    records the cadence timestamp and resets the 429 back-off ladder.
    """
    report = report if report is not None else RunReport()
    work = settings.resolved_work_dir
    work.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    pause_until, pause_days = _read_pause(work)
    if msg := backoff_refusal(pause_until, now):
        _refuse(report, msg)
    if msg := cadence_refusal(_read_last_run(work), now):
        _refuse(report, msg)
    if not settings.ig_username or not _USERNAME.fullmatch(settings.ig_username):
        _refuse(
            report,
            "SIFT_IG_USERNAME is missing or not a valid Instagram username "
            "(letters, digits, '.', '_', max 30 chars); set it in .env — "
            "cookie_fetch targets https://www.instagram.com/<username>/saved/",
        )
    if importlib.util.find_spec("gallery_dl") is None:
        _refuse(
            report,
            "gallery_dl is not importable in this Python environment; reinstall "
            "Sift's deps (`pip install -e .`) — cookie_fetch runs "
            "`python -m gallery_dl`",
        )

    fetch_dir = work / "fetch"
    fetch_dir.mkdir(parents=True, exist_ok=True)
    try:
        proc = _invoke(build_argv(settings))
    except OSError as exc:
        _refuse(report, f"could not launch gallery-dl ({exc}); nothing fetched")
    output = f"{proc.stdout}\n{proc.stderr}"

    if _RATE_LIMIT.search(output):
        days = grow_backoff(pause_days)
        resume = now + timedelta(days=days)
        (work / _PAUSE_FILE).write_text(
            json.dumps({"until": resume.isoformat(), "days": days})
        )
        msg = (
            f"cookie_fetch: Instagram rate-limited this run (HTTP 429); pausing "
            f"further runs for {days} day(s), until {resume:%Y-%m-%d %H:%M} UTC. "
            "Items already downloaded are still processed."
        )
        logger.warning(msg)
        report.errors.append(msg)
    elif proc.returncode != 0:
        lines = (proc.stderr or proc.stdout).strip().splitlines()
        msg = (
            f"cookie_fetch: gallery-dl exited {proc.returncode}"
            f" ({lines[-1] if lines else 'no output'}); check cookies "
            f"(--cookies-from-browser {settings.cookie_browser}) and that you are "
            "logged in. Items already downloaded are still processed."
        )
        logger.warning(msg)
        report.errors.append(msg)
    else:
        (work / _PAUSE_FILE).unlink(missing_ok=True)  # clean run resets back-off
        (work / _LAST_RUN_FILE).write_text(now.isoformat())  # success only

    return _items_from_sidecars(fetch_dir, settings, report)
