"""Adapter: proactive weekly read-only fetch of your NOTE-TO-SELF DM thread. DESIGN.md §4.

RUNS ON THE USER'S MACHINE + HOME IP. READ-ONLY. Never like/follow/comment/DM/send —
no such code path exists: a real Chromium (the user's dedicated Sift profile, logged in
once by hand) opens only the self-thread and reads it. New self-DM'd posts become normalized
`Item`s (permalink + any typed note); their full caption + media are filled in later by the
existing media_defer -> media_queue -> `sift fetch-media` (gallery-dl) path — dm_fetch itself
never opens a post. This is the ONLY recurring authenticated DM read (DESIGN.md §4/§17);
`dyi_export` remains the route for already-sent DM history.

Safety is enforced in code, not prose (trust boundary — exempt from simplification,
CLAUDE.md gate 5). The account-touching cadence + back-off rig is the SAME policy as
cookie_fetch, reused (sibling helpers, not an abstraction layer):
  - Cadence: 20h success floor, 4h attempt floor (cookie_fetch.{cadence,attempt}_refusal).
  - Back-off: a login/checkpoint CHALLENGE pauses future runs 1 day, doubling to a 14-day
    cap (grow_backoff); a clean run resets it. A challenge is NEVER solved automatically —
    dm_fetch stops and asks for a manual `sift dm-login` re-auth.
  - One session ACCOUNT-WIDE: an fcntl lock shared with cookie_fetch (same account);
    a second concurrent run of EITHER adapter — and `sift dm-login` — refuses.
  - Bounds: scrolling and message count are hard-capped; the browser cannot run unbounded.

Trust boundary: every harvested URL / typed note is UNTRUSTED. dm_fetch only normalizes it
into an `Item`; the interpreter's PII-mask + injection sanitize (guardrails) neutralize the
model/embed-bound copy downstream, exactly as for every other adapter. An IG post permalink
routes to the authenticated media queue; any other http(s) URL rides the credential-less,
SSRF-guarded link fetcher; a non-http(s) URL is dropped (the typed note, if any, survives).

The Playwright harvest and `login` are the LIVE seam (mocked in tests). The pure core —
guards, watermark, message->Item transform — is exercised offline. The self-thread is opened
by its stable direct URL (SIFT_DM_THREAD_URL); its shared posts are read from the thread's own
GraphQL responses, NOT the DOM — Instagram renders each share as a click-walled card with no
permalink anchor, so the permalink survives only in the network payload (`target_url`). Both
are login-walled and cannot be verified offline; they are tuned against a real session.
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from ..config import Settings
from ..models import IngestVia, Item, Medium, RunReport
from .cookie_fetch import (
    ACCOUNT_LOCK_FILE,
    _aware,
    _read_stamp,
    attempt_refusal,
    backoff_refusal,
    cadence_refusal,
    grow_backoff,
)
from .dyi_export import _infer_medium, _item_id, canonical_permalink

logger = logging.getLogger(__name__)

_TEXT_CAP = 10_000
_MAX_SCROLLS = 300
_STAGNANT_LIMIT = 3
_MAX_MESSAGES = 5_000
_NAV_TIMEOUT_MS = 45_000
_LOGIN_WAIT_MS = 5 * 60 * 1000
_OFFSCREEN_PX = -32_000

_LAST_RUN_FILE = "dm-fetch-last-run"
_LAST_ATTEMPT_FILE = "dm-fetch-last-attempt"
_PAUSE_FILE = "dm-fetch-pause"
_WATERMARK_FILE = "dm-fetch-watermark"
_LOCK_FILE = ACCOUNT_LOCK_FILE

_INBOX_URL = "https://www.instagram.com/direct/inbox/"
_CHALLENGE_MARKERS = (
    "/accounts/login",
    "/challenge",
    "/accounts/suspended",
    "/accounts/disabled",
)
_THREAD_URL_RE = re.compile(r"^https://www\.instagram\.com/direct/t/\d{5,25}/?$")
_PERMALINK_RE = re.compile(r"/(p|reel|reels|tv)/([A-Za-z0-9_-]{7,15})/")
_GRAPHQL_HINTS = ("/api/graphql", "/graphql/query", "direct_v2", "/api/v1/")


class _ChallengeDetected(RuntimeError):
    """Instagram showed a login wall or checkpoint. Ban-risk signal: stop + grow back-off.
    NEVER solved automatically (that is the behavior that gets accounts banned)."""


# ------------------------------------------------------------------ pure state helpers


def _playwright_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def _refuse(report: RunReport, msg: str) -> None:
    """Refuse loudly: RunReport-visible AND raised (never a silent empty result)."""
    logger.error("dm_fetch: %s", msg)
    report.errors.append(f"dm_fetch: {msg}")
    raise RuntimeError(f"dm_fetch: {msg}")


def _valid_thread_url(raw: str | None) -> str | None:
    """Normalize + validate the configured self-thread URL, or None when unusable.

    ONLY the exact https://www.instagram.com/direct/t/<digits>/ form is accepted: the
    authenticated browser navigates straight here, so a stray host or path must never
    slip through (trust boundary — a bad value fails closed, it does not get scraped).
    """
    if not raw:
        return None
    url = raw.strip()
    if not _THREAD_URL_RE.match(url):
        return None
    return url if url.endswith("/") else url + "/"


def _read_pause(work: Path) -> tuple[datetime | None, int | None]:
    """(pause_until, pause_days) from the state file; (None, None) when absent/corrupt."""
    try:
        data = json.loads((work / _PAUSE_FILE).read_text())
        return _aware(data["until"]), int(data["days"])
    except (OSError, ValueError, TypeError, KeyError):
        return None, None


def _read_watermark(work: Path) -> float:
    """Highest message epoch already ingested; 0.0 when absent/corrupt (degrade to
    'ingest everything and let the dedup ledger dedup', never crash a scheduled run).

    A non-finite (inf/nan) or implausibly-future value is treated as corrupt and reset
    to 0.0 — otherwise one malformed timestamp (e.g. a ms-vs-s selector-tuning slip)
    would poison the watermark and silently black out every future run."""
    try:
        value = float((work / _WATERMARK_FILE).read_text().strip())
    except (OSError, ValueError):
        return 0.0
    ceiling = datetime.now(timezone.utc).timestamp() + 86_400.0
    if not math.isfinite(value) or value < 0.0 or value > ceiling:
        return 0.0
    return value


def _write_watermark(work: Path, ts_epoch: float) -> None:
    ts_epoch = float(ts_epoch)
    assert ts_epoch >= 0.0, "watermark must be non-negative"
    (work / _WATERMARK_FILE).write_text(repr(ts_epoch))


def _ts_to_date(ts_epoch: float) -> date | None:
    """Message epoch -> UTC date, or None when the timestamp is unknown/unreadable.

    None (not today's date) matches dyi_export: an absent timestamp leaves saved_at
    empty rather than stamping every note with whatever day the scheduler fired."""
    ts_epoch = float(ts_epoch or 0.0)
    if ts_epoch <= 0.0:
        return None
    try:
        return datetime.fromtimestamp(ts_epoch, tz=timezone.utc).date()
    except (ValueError, OSError, OverflowError):
        return None


def _usable(msg: dict) -> bool:
    """A message is ingestable if it carries a typed note OR a valid http(s) URL."""
    if str(msg.get("text") or "").strip():
        return True
    split = urlsplit((msg.get("url") or "").strip())
    return bool(split.scheme in ("http", "https") and split.hostname)


def _message_to_item(msg: dict) -> Item:
    """One harvested self-DM message -> normalized Item (ingest_via=dm_fetch).

    A valid http(s) URL becomes the permalink (id keyed on the canonical permalink, so a
    post DM'd here and later saved dedups to one note); any typed note rides as the caption.
    A non-http(s) URL is dropped and the note, if present, becomes a text item."""
    url = (msg.get("url") or "").strip()
    text = str(msg.get("text") or "").strip()[:_TEXT_CAP]
    ts_epoch = float(msg.get("ts") or 0.0)
    assert url or text, "empty message must be filtered by _usable before this call"
    split = urlsplit(url)
    if url and split.scheme in ("http", "https") and split.hostname:
        item = Item(
            id=_item_id(canonical_permalink(url), ""),
            permalink=url,
            medium=_infer_medium(url),
            caption=text,
            ingest_via=IngestVia.dm_fetch,
            saved_at=_ts_to_date(ts_epoch),
        )
        assert item.id and item.permalink, "permalink item must have id + permalink"
        return item
    item = Item(
        id=_item_id(text, str(ts_epoch)),
        medium=Medium.text,
        caption=text,
        ingest_via=IngestVia.dm_fetch,
        saved_at=_ts_to_date(ts_epoch),
    )
    assert item.id and item.permalink is None, "text item must have id and no permalink"
    return item


def _new_messages(raw: list[dict], watermark: float) -> tuple[list[dict], float]:
    """Filter to messages at or newer than the watermark, and return the advanced one.

    The boundary is INCLUSIVE (>=): a new message whose coarse DOM timestamp lands in
    the same bucket as the stored watermark must still reach the dedup ledger, which —
    not the watermark — is the idempotency mechanism. ts == 0.0 (timestamp unreadable)
    always passes. The watermark is only a scroll-depth optimizer."""
    assert watermark >= 0.0, "watermark must be non-negative"
    assert len(raw) <= _MAX_MESSAGES, "raw harvest exceeded its bound"
    fresh: list[dict] = []
    watermark_max = watermark
    for msg in raw:
        ts_epoch = float(msg.get("ts") or 0.0)
        if ts_epoch == 0.0 or ts_epoch >= watermark:
            fresh.append(msg)
        if ts_epoch > watermark_max:
            watermark_max = ts_epoch
    assert watermark_max >= watermark, "watermark must be monotonic non-decreasing"
    assert len(fresh) <= len(raw)
    return fresh, watermark_max


# ------------------------------------------------------------------- live Playwright seam


def _launch_context(pw, settings: Settings, *, visible: bool):
    """Persistent Chromium context on the dedicated Sift profile. Headed (human fingerprint);
    parked off-screen for the background run, on-screen only for interactive `login`.

    The profile holds a LIVE Instagram session; it lives OUTSIDE the (often cloud-synced)
    vault by default (config.resolved_dm_profile_dir) and is chmod 0700 so the session
    cookies are not world-readable."""
    profile = settings.resolved_dm_profile_dir
    assert profile.is_absolute(), "profile dir must be absolute"
    profile.mkdir(parents=True, exist_ok=True)
    try:
        profile.chmod(0o700)
    except OSError as exc:
        logger.warning("dm_fetch: could not chmod 0700 the profile dir (%s)", exc)
    assert profile.is_dir(), "profile dir must exist after mkdir"
    args = [] if visible else [f"--window-position={_OFFSCREEN_PX},{_OFFSCREEN_PX}"]
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=False,
        args=args,
        viewport={"width": 1280, "height": 900},
        locale="en-US",
    )
    context.set_default_timeout(_NAV_TIMEOUT_MS)
    return context


def _assert_logged_in(page) -> None:
    url = page.url or ""
    if any(marker in url for marker in _CHALLENGE_MARKERS):
        raise _ChallengeDetected(f"landed on {url} — session expired or checkpoint")


def _open_self_thread(page, thread_url: str) -> None:
    """Navigate straight to the note-to-self thread by its stable direct URL.

    The inbox list is virtualized + click-walled (rows are JS-click divs, no anchor) and
    the note-to-self row is not reliably locatable by text, so the thread id — which does
    not change — is the deterministic handle. A challenge redirect is turned into
    _ChallengeDetected (ban-risk back-off engages); any OTHER redirect away from
    /direct/t/ means the thread did not open (bad id / lost session) and fails closed, so
    a wrong thread is never scraped."""
    assert thread_url.startswith(
        "https://www.instagram.com/direct/t/"
    ), "validated thread url required"
    page.goto(thread_url, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    landed = page.url or ""
    if any(marker in landed for marker in _CHALLENGE_MARKERS):
        raise _ChallengeDetected(f"landed on {landed} — session expired or checkpoint")
    if "/direct/t/" not in landed:
        raise RuntimeError(
            f"note-to-self thread did not open (redirected to {landed!r}); check "
            "SIFT_DM_THREAD_URL and that the dedicated profile is still logged in"
        )


def _absorb_response(resp, collected: dict[str, dict]) -> None:
    """Pull shared-post permalinks out of one thread GraphQL/API response into `collected`.

    Shared posts render as click-walled cards; their permalink survives only in the
    thread's own network payload (`target_url`), so the harvest reads the JSON, not the
    DOM. Keyed by canonical permalink (reel/tv folded to /p/) so one post shared twice
    collapses to one message. Bounded by _MAX_MESSAGES. Any error (a response already
    gone during SPA churn) skips this body rather than aborting the harvest — every
    permalink already collected survives."""
    try:
        if not any(hint in resp.url for hint in _GRAPHQL_HINTS):
            return
        content_type = resp.headers.get("content-type") or ""
        if "json" not in content_type and "javascript" not in content_type:
            return
        body = resp.text().replace("\\/", "/")
    except Exception:
        return
    for kind, code in _PERMALINK_RE.findall(body):
        if len(collected) >= _MAX_MESSAGES:
            return
        url = f"https://www.instagram.com/{kind}/{code}/"
        key = canonical_permalink(url)
        if key not in collected:
            collected[key] = {"url": url, "text": "", "ts": 0.0}


def _scroll_thread(page, collected: dict[str, dict]) -> None:
    """Scroll the open thread upward (bounded) so Instagram pages older history in; the
    response listener (feeding `collected`) does the extraction.

    Stops early once _STAGNANT_LIMIT consecutive passes add no new permalink (history
    exhausted; the slack absorbs the async gap between a scroll and its GraphQL response)
    or the _MAX_MESSAGES / _MAX_SCROLLS bound is hit — the browser can never run
    unbounded. A wheel error (routine during SPA re-render) ends the scroll cleanly."""
    from playwright.sync_api import Error as PlaywrightError

    stagnant = 0
    previous_count = -1
    for _ in range(_MAX_SCROLLS):
        if len(collected) >= _MAX_MESSAGES:
            break
        if len(collected) == previous_count:
            stagnant += 1
            if stagnant >= _STAGNANT_LIMIT:
                break
        else:
            stagnant = 0
        previous_count = len(collected)
        try:
            page.mouse.move(850, 430)
            page.mouse.wheel(0, -2500)
            page.wait_for_timeout(1000)
        except PlaywrightError:
            break
    assert len(collected) <= _MAX_MESSAGES, "harvest exceeded its bound"


def _harvest(settings: Settings) -> list[dict]:
    """LIVE: open the self-thread by direct URL in the dedicated profile and return raw
    messages read from its GraphQL responses. Offline tests monkeypatch this whole
    function (no browser, no network).

    A response listener is attached BEFORE navigation so the thread's initial page-load
    payload (the newest messages) is captured, then bounded scrolling pages older history
    in. Login state is asserted AFTER the scroll: a checkpoint Instagram raises mid-run
    navigates the page to a challenge URL, turned into a _ChallengeDetected so the
    ban-risk back-off engages instead of the run recording as clean. context.close() is
    isolated so a teardown error cannot mask that challenge."""
    from playwright.sync_api import sync_playwright

    thread_url = _valid_thread_url(settings.dm_thread_url)
    assert thread_url, "dm_thread_url must be validated before _harvest"
    collected: dict[str, dict] = {}
    with sync_playwright() as pw:
        context = _launch_context(pw, settings, visible=False)
        try:
            page = context.new_page()
            page.on("response", lambda resp: _absorb_response(resp, collected))
            _open_self_thread(page, thread_url)
            _assert_logged_in(page)
            _scroll_thread(page, collected)
            _assert_logged_in(page)
        finally:
            try:
                context.close()
            except Exception as exc:
                logger.warning("dm_fetch: browser close failed after harvest (%s)", exc)
    messages = list(collected.values())
    assert len(messages) <= _MAX_MESSAGES, "harvest exceeded its bound"
    return messages


def _warn_if_profile_in_vault(settings: Settings) -> None:
    """Warn (never block) if the browser profile resolves inside the vault: a cloud-synced
    vault would upload the live Instagram session. The default resolves outside the vault;
    this fires only on a deliberate SIFT_DM_PROFILE_DIR override back into it."""
    profile = settings.resolved_dm_profile_dir
    vault = settings.vault_path
    if profile == vault or vault in profile.parents:
        logger.warning(
            "dm_fetch: profile %s is inside the vault %s; a cloud-synced vault would "
            "upload your live Instagram session — set SIFT_DM_PROFILE_DIR to a "
            "non-synced path",
            profile,
            vault,
        )


def login(settings: Settings) -> None:
    """Interactive one-time / re-auth login: open the dedicated profile ON-screen at the
    inbox and wait (bounded) for the user to finish logging in. No terminal input (CLAUDE.md
    rule 6). `sift dm-login` calls this.

    Holds the same account-wide lock as a fetch, so an interactive login and a scheduled
    dm_fetch/cookie_fetch run can never drive two Chromium processes over one profile (which
    corrupts the session). Completion is detected by LEAVING Instagram's login/checkpoint
    pages — any logged-in landing, not only /direct/ — so a home-feed redirect after a valid
    login is not reported as a failure."""
    if not _playwright_available():
        raise RuntimeError(
            "dm_fetch: playwright is not installed; add it with "
            '`uv pip install -e ".[dm_fetch]"` then `python -m playwright install chromium`'
        )
    work = settings.resolved_work_dir
    work.mkdir(parents=True, exist_ok=True)
    lock = (work / _LOCK_FILE).open("w")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError(
                "dm_fetch: an Instagram fetch (cookie_fetch or dm_fetch) is already "
                "running; wait for it to finish, then re-run `sift dm-login` — one "
                "session at a time (DESIGN.md §4)"
            ) from None
        _warn_if_profile_in_vault(settings)
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            context = _launch_context(pw, settings, visible=True)
            try:
                page = context.new_page()
                page.goto(_INBOX_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                page.wait_for_url(
                    lambda url: not any(m in url for m in _CHALLENGE_MARKERS),
                    timeout=_LOGIN_WAIT_MS,
                )
                logger.info("dm_fetch: login persisted to the dedicated Sift profile")
            except PlaywrightTimeout:
                raise RuntimeError(
                    "dm_fetch: login not completed within the 5-minute window; re-run "
                    "`sift dm-login` and finish signing in to Instagram"
                ) from None
            finally:
                try:
                    context.close()
                except PlaywrightError as exc:
                    logger.warning(
                        "dm_fetch: browser close failed after login (%s)", exc
                    )
    finally:
        lock.close()


# ------------------------------------------------------------------------------ pull


def pull(
    settings: Settings, path: str | None = None, report: RunReport | None = None
) -> list[Item]:
    """Read-only Playwright harvest of the self-DM thread -> Items. ingest_via=dm_fetch.

    Guards run FIRST under an exclusive lock (back-off pause, 20h success cadence, 4h attempt
    floor) and refuse loudly. Every real launch stamps the attempt file; only a clean run
    stamps success and clears the back-off ladder. A login/checkpoint challenge pauses future
    runs (never solved automatically); a transient harvest error is logged and yields nothing.
    Idempotent: the dedup ledger (core.run) owns dedup — permalink ids are date-free so a post
    DM'd here and later saved/fetched collapses to one note."""
    report = report if report is not None else RunReport()
    work = settings.resolved_work_dir
    assert work.is_absolute(), "work dir must be absolute"
    work.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    lock = (work / _LOCK_FILE).open("w")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            _refuse(
                report,
                "another Instagram session (cookie_fetch or dm_fetch) is already in "
                "progress (account lock held); refusing a second parallel session "
                "(DESIGN.md §4)",
            )

        pause_until, pause_days = _read_pause(work)
        if msg := backoff_refusal(
            pause_until,
            now,
            who="dm_fetch",
            reason="an Instagram login/checkpoint challenge",
        ):
            _refuse(report, msg)
        if msg := cadence_refusal(
            _read_stamp(work, _LAST_RUN_FILE), now, who="dm_fetch"
        ):
            _refuse(report, msg)
        if msg := attempt_refusal(
            _read_stamp(work, _LAST_ATTEMPT_FILE), now, who="dm_fetch"
        ):
            _refuse(report, msg)

        if _valid_thread_url(settings.dm_thread_url) is None:
            _refuse(
                report,
                "SIFT_DM_THREAD_URL is not set or malformed; dm_fetch opens your "
                "note-to-self thread by direct URL — set it in .env to the form "
                "https://www.instagram.com/direct/t/<thread_id>/",
            )
        if not _playwright_available():
            _refuse(
                report,
                'playwright is not installed; add the extra (`uv pip install -e ".[dm_fetch]"` '
                "then `python -m playwright install chromium`)",
            )

        (work / _LAST_ATTEMPT_FILE).write_text(now.isoformat())
        watermark = _read_watermark(work)

        try:
            raw = _harvest(settings)
        except _ChallengeDetected as exc:
            days = grow_backoff(pause_days)
            resume = now + timedelta(days=days)
            (work / _PAUSE_FILE).write_text(
                json.dumps({"until": resume.isoformat(), "days": days})
            )
            msg = (
                f"Instagram presented a login/checkpoint challenge ({exc}); pausing dm_fetch "
                f"for {days} day(s), until {resume:%Y-%m-%d %H:%M} UTC. Re-run `sift dm-login` "
                "to re-authenticate — a challenge is NEVER solved automatically (DESIGN.md §4)."
            )
            logger.warning("dm_fetch: %s", msg)
            report.errors.append(f"dm_fetch: {msg}")
            return []
        except Exception as exc:
            msg = (
                f"dm_fetch harvest failed ({type(exc).__name__}: {exc}); nothing ingested "
                "this run (state unchanged, next run retries)"
            )
            logger.warning("dm_fetch: %s", msg)
            report.errors.append(msg)
            return []

        fresh, watermark_new = _new_messages(raw, watermark)
        items: list[Item] = []
        for msg in fresh:
            if not _usable(msg):
                skip = f"dm_fetch: empty message skipped ({msg!r})"
                logger.warning(skip)
                report.errors.append(skip)
                continue
            items.append(_message_to_item(msg))

        assert len(items) <= len(fresh), "cannot emit more items than fresh messages"
        _write_watermark(work, watermark_new)
        (work / _PAUSE_FILE).unlink(missing_ok=True)
        (work / _LAST_RUN_FILE).write_text(now.isoformat())
        logger.info("dm_fetch: %d new item(s) from the self-thread", len(items))
        return items
    finally:
        lock.close()
