"""F4 reels/video STT — offline, the model seam monkeypatched (CLAUDE.md Conventions)."""

from __future__ import annotations

from sift.extract import reels_stt, router
from sift.models import IngestVia, Item, Medium


def _item(**kw) -> Item:
    defaults = dict(
        id="reel-1",
        permalink="https://www.instagram.com/p/FAKEREEL/",
        medium=Medium.reel,
        caption="watch this",
        ingest_via=IngestVia.cookie_fetch,
    )
    return Item(**{**defaults, **kw})


def _put_media(settings, name: str) -> str:
    media = settings.vault_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / name).write_bytes(b"fake-mp4-bytes")
    return f"media/{name}"


def test_transcribes_all_files_records_language_and_confidence(settings, monkeypatch):
    files = [_put_media(settings, "a.mp4"), _put_media(settings, "b.m4a")]
    results = iter([("first transcript", "en", 0.9), ("segundo", "es", 0.7)])
    monkeypatch.setattr(reels_stt, "_transcribe", lambda p, s: next(results))

    out = reels_stt.extract(_item(media_files=files), settings)
    assert out.text == "first transcript\n\nsegundo"
    assert out.languages == ["en", "es"]
    assert out.confidence == 0.7  # the weakest file represents the item
    assert out.artifacts == {}


def test_low_confidence_is_flagged_and_kept_never_dropped(settings, monkeypatch):
    files = [_put_media(settings, "mumble.mp4")]
    monkeypatch.setattr(
        reels_stt, "_transcribe", lambda p, s: ("barely audible words", "en", 0.2)
    )
    out = reels_stt.extract(_item(media_files=files), settings)
    assert out.text == "barely audible words"  # kept
    assert out.artifacts == {"low_confidence": ["media/mumble.mp4"]}  # flagged


def test_missing_and_non_media_files_skipped(settings, monkeypatch):
    files = [
        "media/gone.mp4",
        _put_media(settings, "cover.jpg"),
        _put_media(settings, "real.mp4"),
    ]
    monkeypatch.setattr(reels_stt, "_transcribe", lambda p, s: ("spoken", "en", 0.8))
    out = reels_stt.extract(_item(media_files=files), settings)
    assert out.text == "spoken"


def test_one_bad_file_skips_file_not_item(settings, monkeypatch):
    files = [_put_media(settings, "a.mp4"), _put_media(settings, "b.mp4")]
    results = iter([RuntimeError("no audio stream"), ("ok", "en", 0.8)])

    def fake(p, s):
        r = next(results)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(reels_stt, "_transcribe", fake)
    out = reels_stt.extract(_item(media_files=files), settings)
    assert out.text == "ok"


def test_missing_stt_extra_is_caption_only(settings, monkeypatch):
    files = [_put_media(settings, "a.mp4")]

    def no_extra(p, s):
        raise ImportError("No module named 'faster_whisper'")

    monkeypatch.setattr(reels_stt, "_transcribe", no_extra)
    out = reels_stt.extract(_item(media_files=files), settings)
    assert out.text == ""  # interpret() falls back to the caption


def test_soundless_video_is_skipped_not_crashed(settings):
    """A reel with its audio removed used to crash PyAV's stream lookup with a
    bare IndexError; the probe skips it cleanly — real _transcribe, no seam."""
    import pytest

    av = pytest.importorskip("av")
    media = settings.vault_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    silent = media / "silent.mp4"
    with av.open(str(silent), "w") as container:
        stream = container.add_stream("mpeg4", rate=8)
        stream.width = stream.height = 32
        stream.pix_fmt = "yuv420p"
        for _ in range(3):
            container.mux(stream.encode(av.VideoFrame(32, 32, "yuv420p")))
        container.mux(stream.encode(None))

    out = reels_stt.extract(_item(media_files=["media/silent.mp4"]), settings)
    assert out.text == "" and out.confidence is None and out.artifacts == {}


def test_router_dispatches_reel_and_video(settings, monkeypatch):
    marker = object()
    monkeypatch.setattr("sift.extract.reels_stt.extract", lambda i, s: marker)
    for medium in (Medium.reel, Medium.video):
        assert router.extract(_item(medium=medium), settings) is marker
