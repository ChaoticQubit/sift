"""media_fetch offline tests: defer -> SQLite queue -> chunk -> ONE full analysis."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from datetime import date

import frontmatter
import pytest

from sift import core
from sift.db import storage
from sift.guardrails.egress import EgressError
from sift.ingest import cookie_fetch, media_fetch
from sift.ingest.dyi_export import _item_id, canonical_permalink
from sift.models import Extraction, IngestVia, Item, Medium


def _queued_item(shortcode: str, collection: str | None = "Recipes") -> Item:
    url = f"https://www.instagram.com/p/{shortcode}/"
    return Item(
        id=_item_id(canonical_permalink(url), ""),
        permalink=url,
        medium=Medium.image,
        caption=f"caption {shortcode}",
        ingest_via=IngestVia.dyi_export,
        saved_collection=collection,
        saved_at=date(2026, 7, 1),
        source_export="instagram-fake",
    )


@pytest.fixture
def no_rag(monkeypatch):
    """Keep the graph layer out of these tests (covered in test_link.py)."""
    monkeypatch.setattr(core, "_rag", lambda settings: None)


@pytest.fixture(autouse=True)
def healthy_media_env(monkeypatch):
    """These tests never touch real media or a real embedder; pretend both
    toolchain checks pass (each has its own refusal test below)."""
    monkeypatch.setattr(media_fetch.shutil, "which", lambda name: "/fake/ffmpeg")
    monkeypatch.setattr(media_fetch, "_embed_ready", lambda settings: None)


@pytest.fixture
def offline_extract(monkeypatch):
    monkeypatch.setattr(
        "sift.extract.router.extract",
        lambda item, settings: Extraction(text=item.caption or "OCR TEXT"),
    )


def _ok_invoke(monkeypatch, stdout="", stderr="", rc=0):
    monkeypatch.setattr(
        cookie_fetch,
        "_invoke",
        lambda argv: subprocess.CompletedProcess(argv, rc, stdout, stderr),
    )


def _clear_attempt_floor(settings):
    (settings.resolved_work_dir / media_fetch._LAST_ATTEMPT_FILE).unlink(
        missing_ok=True
    )


def _write_sidecar(settings, item: Item) -> None:
    fetch = settings.resolved_work_dir / "fetch"
    fetch.mkdir(parents=True, exist_ok=True)
    (fetch / "a.jpg").write_bytes(b"\xff\xd8fake-jpeg")
    (fetch / "a.jpg.json").write_text(
        json.dumps(
            {
                "post_url": item.permalink,
                "description": "sidecar caption",
                "date": "2026-07-02 03:04:05",
            }
        )
    )


def test_run_defers_instagram_items_to_queue(
    settings, dyi_zip, fake_model, no_rag, offline_extract
):
    defer = settings.model_copy(update={"media_defer": True})
    report = core.run("dyi_export", defer, str(dyi_zip))
    assert report.deferred > 0
    assert report.written >= 1  # DM media/text items still process normally
    counts = storage.media_counts(settings)
    assert counts.get("pending") == report.deferred
    # No note exists for a deferred item, and a rerun re-queues nothing.
    report2 = core.run("dyi_export", defer, str(dyi_zip))
    assert report2.deferred == 0 and report2.skipped >= report.deferred


def test_chunk_fetches_analyzes_and_preserves_provenance(
    settings, fake_model, no_rag, offline_extract, monkeypatch
):
    for code in ("AAA", "BBB", "CCC"):
        assert storage.enqueue_media(_queued_item(code), settings)
    # Same-second enqueues tie on enqueued_at; age AAA so the chunk of 2 must include it.
    sqlite3.connect(settings.resolved_work_dir / "ledger.db").execute(
        "UPDATE media_queue SET enqueued_at = '2026-01-01' WHERE item_id = ?",
        (_queued_item("AAA").id,),
    ).connection.commit()
    small = settings.model_copy(update={"media_chunk_size": 2})
    _write_sidecar(settings, _queued_item("AAA"))
    _ok_invoke(monkeypatch)

    report = media_fetch.run(small)

    counts = storage.media_counts(settings)
    assert counts == {"pending": 2, "done": 1}  # even chunk of 2: one hit, one miss
    notes = list((settings.vault_path / "Captures").glob("*.md"))
    assert len(notes) == 1
    post = frontmatter.load(notes[0])
    assert post.metadata["media_files"]  # real media attached
    assert post.metadata["saved_collection"] == "Recipes"  # import provenance kept
    assert post.metadata["caption"] == "caption AAA"  # payload caption wins
    assert report.written == 1


def test_missing_media_retries_then_caption_only(
    settings, fake_model, no_rag, offline_extract, monkeypatch
):
    storage.enqueue_media(_queued_item("GONE"), settings)
    _ok_invoke(monkeypatch)
    for _ in range(media_fetch._MAX_ATTEMPTS):
        _clear_attempt_floor(settings)
        media_fetch.run(settings)
    assert storage.media_counts(settings) == {"nomedia": 1}
    notes = list((settings.vault_path / "Captures").glob("*.md"))
    assert len(notes) == 1  # caption-only note — never silently dropped
    assert not frontmatter.load(notes[0]).metadata["media_files"]


def test_rate_limit_pauses_both_fetchers_and_burns_no_attempts(
    settings, fake_model, no_rag, offline_extract, monkeypatch
):
    storage.enqueue_media(_queued_item("RATED"), settings)
    _ok_invoke(monkeypatch, stderr="HttpError: '429 Too Many Requests'", rc=4)
    media_fetch.run(settings)

    work = settings.resolved_work_dir
    assert (work / cookie_fetch._PAUSE_FILE).exists()  # SHARED back-off engaged
    with pytest.raises(RuntimeError, match="paused"):
        cookie_fetch.pull(settings)  # ...and it pauses cookie_fetch too
    rows = (
        sqlite3.connect(work / "ledger.db")
        .execute("SELECT status, attempts FROM media_queue")
        .fetchall()
    )
    assert rows == [("pending", 0)]  # not the post's fault — no attempt burned


def test_attempt_floor_refuses_rapid_chunks(
    settings, fake_model, no_rag, offline_extract, monkeypatch
):
    storage.enqueue_media(_queued_item("FLOOR"), settings)
    _ok_invoke(monkeypatch)
    media_fetch.run(settings)
    with pytest.raises(RuntimeError, match="daily-paced"):
        media_fetch.run(settings)


def test_empty_queue_is_a_clean_noop(settings, no_rag, monkeypatch):
    called = []
    monkeypatch.setattr(cookie_fetch, "_invoke", lambda argv: called.append(argv))
    report = media_fetch.run(settings)
    assert not called and report.processed == 0  # no gallery-dl launch at all


def test_profile_links_never_defer_and_never_reach_gallery_dl(
    settings, fake_model, no_rag, offline_extract, monkeypatch
):
    from sift.guardrails.egress import is_instagram_post_url

    # A shared PROFILE link is not a post: it must not be deferred...
    profile = Item(
        id="prof1",
        permalink="https://www.instagram.com/_u/someshop",
        medium=Medium.image,
        caption="check this shop",
        ingest_via=IngestVia.dyi_export,
    )
    assert not core._defer_to_media_queue(
        profile, settings.model_copy(update={"media_defer": True})
    )
    assert is_instagram_post_url("https://www.instagram.com/p/ABC123/")
    assert not is_instagram_post_url("https://www.instagram.com/_u/someshop")
    assert not is_instagram_post_url("https://www.instagram.com/someshop")

    # ...and a legacy queue row holding one is closed out caption-only, with the
    # profile URL never passed to gallery-dl.
    storage.enqueue_media(profile, settings)
    invoked = []
    monkeypatch.setattr(
        cookie_fetch,
        "_invoke",
        lambda argv: invoked.append(argv)
        or subprocess.CompletedProcess(argv, 0, "", ""),
    )
    media_fetch.run(settings)
    assert invoked == []  # nothing fetchable -> gallery-dl never launched
    assert storage.media_counts(settings) == {"nomedia": 1}
    assert len(list((settings.vault_path / "Captures").glob("*.md"))) == 1


def test_analysis_overlaps_download(
    settings, fake_model, no_rag, offline_extract, monkeypatch
):
    """The pipeline: a landed post is analyzed WHILE gallery-dl still runs."""
    storage.enqueue_media(_queued_item("LIVE"), settings)
    monkeypatch.setattr(media_fetch, "_POLL_SECONDS", 0.05)
    monkeypatch.setattr(media_fetch, "_SETTLE_SECONDS", 0.0)
    captures = settings.vault_path / "Captures"

    def slow_invoke(argv):
        # "Download" LIVE's post, then keep the fake gallery-dl alive until the
        # main thread has written its note — proof of overlap, not sequencing.
        _write_sidecar(settings, _queued_item("LIVE"))
        deadline = time.time() + 10
        while time.time() < deadline:
            if list(captures.glob("*.md")):
                return subprocess.CompletedProcess(argv, 0, "", "")
            time.sleep(0.05)
        raise AssertionError("note never appeared while download still running")

    monkeypatch.setattr(cookie_fetch, "_invoke", slow_invoke)
    report = media_fetch.run(settings)
    assert report.written == 1 and report.processed == 1
    assert storage.media_counts(settings) == {"done": 1}


def test_want_holds_back_unsettled_and_processed_posts(settings, tmp_path):
    canon = canonical_permalink("https://www.instagram.com/p/ZZZ/")
    iid = _item_id(canon, "")
    mine = {iid}
    f = tmp_path / "123_1.jpg"
    f.write_bytes(b"x")

    fresh = media_fetch._want(mine, set(), False, tmp_path)
    assert not fresh(canon, [f])  # just landed: inside the settle window

    old = time.time() - 3600
    os.utime(f, (old, old))
    assert media_fetch._want(mine, set(), False, tmp_path)(canon, [f])  # settled
    # A prior chunk's post is never rescanned mid-run (its media would re-copy)...
    assert not media_fetch._want(set(), set(), False, tmp_path)(canon, [f])
    # ...but the final sweep still sees it, matching pre-pipeline behavior.
    assert media_fetch._want(set(), set(), True, tmp_path)(canon, [f])

    (tmp_path / "123_2.mp4.part").write_bytes(b"")
    assert not media_fetch._want(mine, set(), False, tmp_path)(canon, [f])  # in flight

    assert not media_fetch._want(mine, {iid}, False, tmp_path)(canon, [f])
    assert not media_fetch._want(mine, {iid}, True, tmp_path)(canon, [f])  # even final


def test_missing_ffmpeg_refuses_before_burning_attempts(settings, monkeypatch):
    storage.enqueue_media(_queued_item("REELY"), settings)
    monkeypatch.setattr(media_fetch.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="ffmpeg"):
        media_fetch.run(settings)
    assert storage.media_counts(settings) == {"pending": 1}  # nothing burned


def test_dead_embedder_refuses_before_burning_attempts(settings, monkeypatch):
    storage.enqueue_media(_queued_item("EMBED"), settings)
    monkeypatch.setattr(media_fetch, "_embed_ready", lambda s: "HTTP 500")
    with pytest.raises(RuntimeError, match="embedding backend"):
        media_fetch.run(settings)
    assert storage.media_counts(settings) == {"pending": 1}  # nothing burned


def test_build_argv_gates_every_chunk_url(settings):
    with pytest.raises(EgressError):
        cookie_fetch.build_argv(
            settings, ["https://www.instagram.com/p/OK/", "https://evil.com/p/x/"]
        )
    argv = cookie_fetch.build_argv(settings, ["https://www.instagram.com/p/OK/"])
    assert argv[-1] == "https://www.instagram.com/p/OK/"
    assert "--config-ignore" in argv and "--write-metadata" in argv
