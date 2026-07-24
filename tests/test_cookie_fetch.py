"""cookie_fetch adapter — fully OFFLINE: the gallery-dl subprocess is a mocked seam,
sidecars are synthetic fixtures, and no real browser cookies are ever touched."""

from __future__ import annotations

import json
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from sift import core
from sift.ingest import cookie_fetch
from sift.models import Medium, RunReport

UTC = timezone.utc
NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

CDN = "https://scontent.cdninstagram.com/v/t51.2885-15"


@pytest.fixture
def ig_settings(settings):
    # Explicit value beats any ambient SIFT_IG_USERNAME (conftest convention).
    return settings.model_copy(update={"ig_username": "fake_user"})


@pytest.fixture
def fake_gdl(monkeypatch):
    """Mock the subprocess seam (offline rule). `setup` runs at 'download time' so a
    test can drop sidecar fixtures into fetch/ exactly when gallery-dl would."""
    seam = SimpleNamespace(argv=None, returncode=0, stdout="", stderr="", setup=None)

    def _fake(argv):
        seam.argv = argv
        if seam.setup:
            seam.setup()
        return SimpleNamespace(
            returncode=seam.returncode, stdout=seam.stdout, stderr=seam.stderr
        )

    monkeypatch.setattr(cookie_fetch, "_invoke", _fake)
    return seam


def _work(settings):
    return settings.resolved_work_dir


def _sidecar(settings, name, media=b"\xff\xd8fake", **extra):
    """A downloaded file + its --write-metadata JSON sidecar, gallery-dl shape."""
    fetch = _work(settings) / "fetch"
    fetch.mkdir(parents=True, exist_ok=True)
    meta = {
        "post_url": "https://www.instagram.com/p/DEFAULT01/",
        "description": "a caption",
        "date": "2026-07-15 12:00:00",
        "display_url": f"{CDN}/{name}",
        "video_url": None,
        "type": "post",
        "username": "someone",
    }
    meta.update(extra)
    if media is not None:
        (fetch / name).write_bytes(media)
    (fetch / f"{name}.json").write_text(json.dumps(meta))


def _pull(settings):
    report = RunReport()
    return cookie_fetch.pull(settings, None, report), report


def _stale(settings, *names, hours=30):
    """Age state stamps so the next pull passes the cadence/attempt guards."""
    for n in names:
        (_work(settings) / n).write_text(
            (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        )


# ------------------------------------------------------------------ argv construction


def test_argv_exact_and_read_only(ig_settings):
    work = _work(ig_settings)
    argv = cookie_fetch.build_argv(ig_settings)
    assert argv == [
        sys.executable,
        "-m",
        "gallery_dl",
        # ambient user config must never reach an account-touching run: it could
        # export the browser session cookies to disk, add a proxy, or run commands
        "--config-ignore",
        "--cookies-from-browser",
        ig_settings.cookie_browser,  # never asserted literal: ambient env may differ
        "--sleep-request",
        "6.0-12.0",
        "--download-archive",
        str(work / "gallery-dl-archive.db"),
        "--write-metadata",
        "--directory",
        str(work / "fetch"),
        "https://www.instagram.com/fake_user/saved/",
    ]
    # sleep-request floor >= 3s (DESIGN.md §4), range form included
    low = argv[argv.index("--sleep-request") + 1].partition("-")[0]
    assert float(low) >= 3.0
    # strictly read-only: no extractor options / interaction flags of any kind
    assert "-o" not in argv and "--option" not in argv
    # the target itself passes the Instagram permalink gate
    from sift.guardrails.egress import assert_instagram_permalink

    assert_instagram_permalink(argv[-1])


# ---------------------------------------------------------------------- pure guards


def test_cadence_refusal_pure():
    assert cookie_fetch.cadence_refusal(None, NOW) is None
    assert cookie_fetch.cadence_refusal(NOW - timedelta(hours=20), NOW) is None
    assert cookie_fetch.cadence_refusal(NOW - timedelta(days=7), NOW) is None
    msg = cookie_fetch.cadence_refusal(NOW - timedelta(hours=19), NOW)
    assert msg and "never hourly" in msg and "2026-07-20" in msg
    assert cookie_fetch.cadence_refusal(NOW - timedelta(minutes=5), NOW)


def test_attempt_refusal_pure():
    assert cookie_fetch.attempt_refusal(None, NOW) is None
    assert cookie_fetch.attempt_refusal(NOW - timedelta(hours=4), NOW) is None
    msg = cookie_fetch.attempt_refusal(NOW - timedelta(hours=1), NOW)
    assert msg and "did not succeed" in msg and "2026-07-20" in msg


def test_backoff_refusal_pure():
    assert cookie_fetch.backoff_refusal(None, NOW) is None
    assert cookie_fetch.backoff_refusal(NOW - timedelta(seconds=1), NOW) is None
    msg = cookie_fetch.backoff_refusal(NOW + timedelta(days=2), NOW)
    assert msg and "429" in msg and "2026-07-22" in msg  # says until when


def test_grow_backoff_exponential_days_capped():
    assert cookie_fetch.grow_backoff(None) == 1
    assert cookie_fetch.grow_backoff(0) == 1
    assert cookie_fetch.grow_backoff(1) == 2
    assert cookie_fetch.grow_backoff(2) == 4
    assert cookie_fetch.grow_backoff(4) == 8
    assert cookie_fetch.grow_backoff(8) == 14  # capped
    assert cookie_fetch.grow_backoff(14) == 14


# ---------------------------------------------------------------- guard integration


def test_cadence_refuses_within_20h_passes_after(ig_settings, fake_gdl):
    items, report = _pull(ig_settings)  # clean run records the timestamp
    assert (_work(ig_settings) / "cookie-fetch-last-run").exists()

    report2 = RunReport()
    with pytest.raises(RuntimeError, match="never hourly"):
        cookie_fetch.pull(ig_settings, None, report2)
    assert any("refusing to fetch" in e for e in report2.errors)  # RunReport-visible

    # >= 20h later the same state allows a run again
    _stale(ig_settings, "cookie-fetch-last-run", "cookie-fetch-last-attempt")
    items, _ = _pull(ig_settings)  # no raise
    assert items == []


def test_429_sets_pause_grows_and_resets(ig_settings, fake_gdl):
    work = _work(ig_settings)
    # gallery-dl's actual surface (extractor/common.py): retry wait log + HttpError
    fake_gdl.returncode = 4
    fake_gdl.stderr = (
        "[instagram][info] Waiting for 1 minutes until 12:01:00 "
        "(429 Too Many Requests)\n"
        "[instagram][error] HttpError: '429 Too Many Requests' for "
        "'https://www.instagram.com/api/v1/feed/saved/posts/'"
    )
    _, report = _pull(ig_settings)
    pause = json.loads((work / "cookie-fetch-pause.json").read_text())
    assert pause["days"] == 1
    assert any("pausing" in e and "1 day" in e for e in report.errors)
    assert not (work / "cookie-fetch-last-run").exists()  # 429 run is not a success

    # while paused: refuse, with the resume date in the message
    until = datetime.fromisoformat(pause["until"])
    report2 = RunReport()
    with pytest.raises(RuntimeError, match=f"{until:%Y-%m-%d}"):
        cookie_fetch.pull(ig_settings, None, report2)
    assert any("paused until" in e for e in report2.errors)

    # pause elapsed + rate-limited again -> exponential growth (1 -> 2 days)
    expired = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    (work / "cookie-fetch-pause.json").write_text(
        json.dumps({"until": expired, "days": 1})
    )
    _stale(ig_settings, "cookie-fetch-last-attempt")
    _pull(ig_settings)
    assert json.loads((work / "cookie-fetch-pause.json").read_text())["days"] == 2

    # a clean run resets the ladder
    (work / "cookie-fetch-pause.json").write_text(
        json.dumps({"until": expired, "days": 2})
    )
    _stale(ig_settings, "cookie-fetch-last-attempt")
    fake_gdl.returncode, fake_gdl.stderr = 0, ""
    _pull(ig_settings)
    assert not (work / "cookie-fetch-pause.json").exists()
    assert (work / "cookie-fetch-last-run").exists()


def test_429_partial_results_still_processed(ig_settings, fake_gdl):
    fake_gdl.returncode = 4
    fake_gdl.stderr = "HttpError: '429 Too Many Requests' for 'https://x'"
    fake_gdl.setup = lambda: _sidecar(
        ig_settings, "partial.jpg", post_url="https://www.instagram.com/p/PART01/"
    )
    items, report = _pull(ig_settings)
    assert [i.permalink for i in items] == ["https://www.instagram.com/p/PART01/"]
    assert any("429" in e for e in report.errors)


def test_failed_run_stamps_attempt_and_enforces_floor(ig_settings, fake_gdl):
    """Account-safety regression: a persistently failing session (expired cookies,
    exit without a 429 marker) must NOT be retryable with unbounded frequency —
    scheduling.md's 'over-scheduling is safe' claim in the expected failure mode."""
    work = _work(ig_settings)
    fake_gdl.returncode = 16  # gallery-dl AuthorizationError: expired/invalid cookies
    fake_gdl.stderr = "[instagram][error] AuthorizationError: login required"
    _, report = _pull(ig_settings)  # failed run: reported, not raised
    assert any("exited 16" in e for e in report.errors)
    assert not (work / "cookie-fetch-last-run").exists()  # not a success
    assert (work / "cookie-fetch-last-attempt").exists()  # but the attempt counts

    # the hourly-cron scenario: the very next run refuses BEFORE any subprocess
    fake_gdl.argv = None
    report2 = RunReport()
    with pytest.raises(RuntimeError, match="did not succeed"):
        cookie_fetch.pull(ig_settings, None, report2)
    assert any("refusing another attempt" in e for e in report2.errors)
    assert fake_gdl.argv is None  # no real launch

    # once the attempt floor has elapsed, a retry is allowed again
    _stale(ig_settings, "cookie-fetch-last-attempt", hours=5)
    _pull(ig_settings)
    assert fake_gdl.argv is not None


def test_corrupt_naive_state_guards_instead_of_crashing(ig_settings, fake_gdl):
    """A hand-edited/corrupted state file with a NAIVE ISO timestamp must degrade
    to a working guard (assumed UTC), never an uncaught TypeError in pull()."""
    work = _work(ig_settings)
    work.mkdir(parents=True, exist_ok=True)
    naive = (datetime.now(UTC) - timedelta(hours=5)).replace(tzinfo=None)
    (work / "cookie-fetch-last-run").write_text(naive.isoformat())
    with pytest.raises(RuntimeError, match="never hourly"):  # NOT TypeError
        _pull(ig_settings)

    (work / "cookie-fetch-last-run").unlink()
    naive_until = (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None)
    (work / "cookie-fetch-pause.json").write_text(
        json.dumps({"until": naive_until.isoformat(), "days": 1})
    )
    with pytest.raises(RuntimeError, match="paused until"):
        _pull(ig_settings)

    # outright garbage is treated as absent state: the run proceeds
    (work / "cookie-fetch-pause.json").unlink()
    (work / "cookie-fetch-last-run").write_text("not a timestamp")
    _pull(ig_settings)
    assert fake_gdl.argv is not None


def test_concurrent_run_refused_by_lock(ig_settings, fake_gdl):
    """Account-safety regression: two simultaneous runs (cron racing a manual run)
    must never open two parallel authenticated Instagram sessions."""
    import fcntl

    work = _work(ig_settings)
    work.mkdir(parents=True, exist_ok=True)
    holder = (work / "cookie-fetch.lock").open("w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)  # the "other" run
    try:
        report = RunReport()
        with pytest.raises(RuntimeError, match="already in progress"):
            cookie_fetch.pull(ig_settings, None, report)
        assert any("already in progress" in e for e in report.errors)
        assert fake_gdl.argv is None  # never got anywhere near a subprocess
    finally:
        holder.close()
    _pull(ig_settings)  # lock released -> runs normally
    assert fake_gdl.argv is not None


def test_hung_subprocess_killed_and_reported(ig_settings, monkeypatch):
    def _hang(argv):
        raise cookie_fetch.subprocess.TimeoutExpired(argv, 14400)

    monkeypatch.setattr(cookie_fetch, "_invoke", _hang)
    _, report = _pull(ig_settings)  # reported, not raised; sidecars still processed
    assert any("killed" in e for e in report.errors)
    assert not (_work(ig_settings) / "cookie-fetch-last-run").exists()


def test_missing_username_and_bad_username_refused(settings, fake_gdl):
    for bad in (None, "", "../evil", "a b", "x" * 31):
        report = RunReport()
        with pytest.raises(RuntimeError, match="SIFT_IG_USERNAME"):
            cookie_fetch.pull(
                settings.model_copy(update={"ig_username": bad}), None, report
            )
        assert any("SIFT_IG_USERNAME" in e for e in report.errors)
    assert fake_gdl.argv is None  # never got anywhere near a subprocess


def test_gallery_dl_not_importable_refused(ig_settings, fake_gdl, monkeypatch):
    monkeypatch.setattr(cookie_fetch.importlib.util, "find_spec", lambda name: None)
    report = RunReport()
    with pytest.raises(RuntimeError, match="pip install"):
        cookie_fetch.pull(ig_settings, None, report)
    assert any("gallery_dl" in e for e in report.errors)
    assert fake_gdl.argv is None


# ------------------------------------------------------------- sidecar -> Item mapping


def test_sidecar_mapping_caption_medium_date_media(ig_settings, fake_gdl):
    def setup():
        # mojibake-mangled caption (latin-1 escaped UTF-8) must come out repaired
        _sidecar(
            ig_settings,
            "single.jpg",
            post_url="https://www.instagram.com/p/SINGLE01/",
            description="It\u00e2\u0080\u0099s the best \u00e2\u0080\u0094 save it",
        )
        # already-clean emoji caption must survive untouched
        _sidecar(
            ig_settings,
            "reelvid.mp4",
            post_url="https://www.instagram.com/reel/REEL0001/",
            description="\U0001f36a reel caption",
            video_url=f"{CDN}/reelvid.mp4",
            type="reel",
        )
        # two files, one post_url = carousel
        for n in ("car_1.jpg", "car_2.jpg"):
            _sidecar(
                ig_settings,
                n,
                post_url="https://www.instagram.com/p/CAROUSEL1/",
                description="carousel caption",
            )

    fake_gdl.setup = setup
    items, report = _pull(ig_settings)
    by_link = {i.permalink: i for i in items}
    assert len(items) == 3

    single = by_link["https://www.instagram.com/p/SINGLE01/"]
    assert single.caption == "It’s the best — save it"  # demojibaked
    assert single.medium is Medium.image
    assert single.saved_at == date(2026, 7, 15)
    assert single.ingest_via.value == "cookie_fetch"
    # Staged in place, NOT copied into the vault — core._dispose_media decides
    # at note-writing time whether the vault keeps anything.
    assert single.media_files == [str(_work(ig_settings) / "fetch" / "single.jpg")]
    assert not (ig_settings.vault_path / "media" / "single.jpg").exists()

    reel = by_link["https://www.instagram.com/reel/REEL0001/"]
    assert reel.medium is Medium.reel
    assert reel.caption == "\U0001f36a reel caption"

    carousel = by_link["https://www.instagram.com/p/CAROUSEL1/"]
    assert carousel.medium is Medium.carousel
    fetch = _work(ig_settings) / "fetch"
    assert carousel.media_files == [str(fetch / "car_1.jpg"), str(fetch / "car_2.jpg")]
    assert report.guardrail_blocks == 0


def test_host_filter_subdomain_aware(ig_settings, fake_gdl):
    def setup():
        _sidecar(  # suffix lookalike host must be REJECTED
            ig_settings,
            "evil.jpg",
            post_url="https://www.instagram.com/p/EVIL0001/",
            display_url="https://evil-instagram.com.attacker.net/x.jpg",
        )
        _sidecar(  # real CDN subdomain must PASS
            ig_settings,
            "good.jpg",
            post_url="https://www.instagram.com/p/GOOD0001/",
            display_url="https://scontent.cdninstagram.com/v/good.jpg",
        )
        _sidecar(  # non-instagram permalink must be REJECTED too
            ig_settings,
            "offsite.jpg",
            post_url="https://example.com/p/NOTIG001/",
        )

    fake_gdl.setup = setup
    items, report = _pull(ig_settings)
    assert [i.permalink for i in items] == ["https://www.instagram.com/p/GOOD0001/"]
    assert report.guardrail_blocks == 2  # logged + counted + skipped
    assert sum("non-Instagram host" in e for e in report.errors) == 2


# ----------------------------------------------------------------------------- dedup


def test_same_permalink_via_export_and_fetch_dedups(settings, fake_gdl, fake_model):
    link = "https://www.instagram.com/reel/DEDUPE01/"
    zp = settings.vault_path.parent / "instagram-dedupe.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr(
            "your_instagram_activity/saved/saved_posts.json",
            json.dumps(
                {
                    "saved_saved_media": [
                        {
                            "title": "poster",
                            "string_map_data": {
                                "Saved on": {"href": link, "timestamp": 1721001600}
                            },
                        }
                    ]
                }
            ),
        )
    report = core.run("dyi_export", settings, path=str(zp))
    assert (report.written, report.skipped) == (1, 0)

    ig = settings.model_copy(update={"ig_username": "fake_user"})
    fake_gdl.setup = lambda: _sidecar(
        ig, "dedupe.mp4", post_url=link, video_url=f"{CDN}/d.mp4", type="reel"
    )
    report2 = core.run("cookie_fetch", ig)
    # same permalink -> same stable id -> the ledger dedups to ONE note
    assert (report2.processed, report2.written, report2.skipped) == (1, 0, 1)


def test_reel_p_and_tv_permalink_forms_share_one_id():
    """gallery-dl emits /p/<code>/ for any reel with audio metadata (the appended
    audio entry defeats its len(files)==1 reel check) while the DYI export and DM
    shares use /reel/<code>/; shortcodes share one namespace, so all forms must
    canonicalize — and therefore hash — identically."""
    from sift.ingest.dyi_export import _item_id, canonical_permalink

    p = canonical_permalink("https://www.instagram.com/p/CODE123/")
    assert canonical_permalink("https://www.instagram.com/reel/CODE123/") == p
    assert canonical_permalink("https://www.instagram.com/reels/CODE123") == p
    assert canonical_permalink("https://www.instagram.com/tv/CODE123/?igsh=x") == p
    assert _item_id(canonical_permalink("https://www.instagram.com/reel/C1/"), "") == (
        _item_id(canonical_permalink("https://www.instagram.com/p/C1/"), "")
    )
    # non-Instagram links are untouched (query may be identity there)
    other = "https://example.com/reel/CODE123/?q=1"
    assert canonical_permalink(other) == other


def test_music_reel_p_form_dedups_with_export_reel_form(settings, fake_gdl, fake_model):
    """The headline cross-adapter dedup for the most common saved type: the export
    saves /reel/<code>/, gallery-dl's sidecar for the same (music) reel says
    /p/<code>/ with type="post" — one id, one note, and Medium.reel either way."""
    zp = settings.vault_path.parent / "instagram-musicreel.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr(
            "your_instagram_activity/saved/saved_posts.json",
            json.dumps(
                {
                    "saved_saved_media": [
                        {
                            "title": "poster",
                            "string_map_data": {
                                "Saved on": {
                                    "href": "https://www.instagram.com/reel/MUSIC001/",
                                    "timestamp": 1721001600,
                                }
                            },
                        }
                    ]
                }
            ),
        )
    report = core.run("dyi_export", settings, path=str(zp))
    assert (report.written, report.skipped) == (1, 0)

    ig = settings.model_copy(update={"ig_username": "fake_user"})
    # exactly what gallery-dl 1.32.7 writes for a reel with original audio:
    fake_gdl.setup = lambda: _sidecar(
        ig,
        "music.mp4",
        post_url="https://www.instagram.com/p/MUSIC001/",  # NOT /reel/
        video_url=f"{CDN}/music.mp4",
        type="post",  # NOT "reel"
    )
    items, _ = _pull(ig)
    assert items[0].medium is Medium.reel  # type field must not be trusted
    _stale(ig, "cookie-fetch-last-run", "cookie-fetch-last-attempt")
    report2 = core.run("cookie_fetch", ig)
    # same shortcode -> same stable id -> the ledger dedups to ONE note
    assert (report2.processed, report2.written, report2.skipped) == (1, 0, 1)


# ------------------------------------------------------------------------ end to end


def test_end_to_end_core_run_and_second_run_skips(ig_settings, fake_gdl, fake_model):
    def setup():
        _sidecar(
            ig_settings,
            "e2e_1.jpg",
            post_url="https://www.instagram.com/p/E2EPOST1/",
            description="first caption",
        )
        _sidecar(
            ig_settings,
            "e2e_2.mp4",
            post_url="https://www.instagram.com/reel/E2EREEL2/",
            description="second caption",
            video_url=f"{CDN}/e2e_2.mp4",
            type="reel",
        )

    fake_gdl.setup = setup
    report = core.run("cookie_fetch", ig_settings)
    assert (report.processed, report.written, report.skipped) == (2, 2, 0)
    assert len(report.notes) == 2
    import frontmatter

    notes = [frontmatter.loads(Path(p).read_text()) for p in report.notes]
    first = next(n for n in notes if n["caption"] == "first caption")
    assert first["ingest_via"] == "cookie_fetch"

    # second scheduled run: archive/ledger make it a no-op (all skipped)
    _stale(ig_settings, "cookie-fetch-last-run", "cookie-fetch-last-attempt")
    report2 = core.run("cookie_fetch", ig_settings)
    assert (report2.processed, report2.written, report2.skipped) == (2, 0, 2)
