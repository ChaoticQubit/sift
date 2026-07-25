"""F4 reels/video STT + frame OCR — offline, the model seams monkeypatched
(CLAUDE.md Conventions); the ffmpeg frame-extraction tests use real media."""

from __future__ import annotations

import shutil

import pytest

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


def test_soundless_video_is_skipped_not_crashed(settings, monkeypatch):
    """A reel with its audio removed used to crash PyAV's stream lookup with a
    bare IndexError; the probe skips it cleanly — real _transcribe, no seam."""
    monkeypatch.setattr(reels_stt, "_frames_text", lambda p, s: "")  # no OCR here
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


def test_frame_cap_scales_with_duration():
    assert reels_stt._frame_cap(0) == 20  # unknown duration -> treated short
    assert reels_stt._frame_cap(59.9) == 20
    assert reels_stt._frame_cap(60) == 50
    assert reels_stt._frame_cap(300) == 50
    assert reels_stt._frame_cap(300.1) == 100


def test_onscreen_text_joins_the_transcript(settings, monkeypatch):
    files = [_put_media(settings, "a.mp4")]
    monkeypatch.setattr(reels_stt, "_transcribe", lambda p, s: ("spoken", "en", 0.9))
    monkeypatch.setattr(reels_stt, "_frames_text", lambda p, s: "ON-SCREEN LIST")
    out = reels_stt.extract(_item(media_files=files), settings)
    assert out.text == "spoken\n\nON-SCREEN LIST"


def test_frame_ocr_failure_is_transcript_only_never_fatal(settings, monkeypatch):
    files = [_put_media(settings, "a.mp4")]
    monkeypatch.setattr(reels_stt, "_transcribe", lambda p, s: ("spoken", "en", 0.9))

    def boom(p, s):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(reels_stt, "_frames_text", boom)
    out = reels_stt.extract(_item(media_files=files), settings)
    assert out.text == "spoken"


def _two_scene_video(settings) -> str:
    """A real mp4: 8 red frames then 8 blue — exactly one scene change."""
    np = pytest.importorskip("numpy")
    av = pytest.importorskip("av")
    media = settings.vault_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    path = media / "scenes.mp4"
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=8)
        stream.width = stream.height = 64
        stream.pix_fmt = "yuv420p"
        for color in ((255, 0, 0), (0, 0, 255)):
            rgb = np.full((64, 64, 3), color, dtype=np.uint8)
            for _ in range(8):
                container.mux(stream.encode(av.VideoFrame.from_ndarray(rgb, "rgb24")))
        container.mux(stream.encode(None))
    return str(path)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_unique_frames_keeps_first_frame_and_scene_changes_only(settings, tmp_path):
    video = _two_scene_video(settings)
    full = tmp_path / "full"
    full.mkdir()
    assert len(reels_stt._unique_frames(video, full, cap=10)) == 2  # 16 in, 2 unique
    capped = tmp_path / "capped"
    capped.mkdir()
    assert len(reels_stt._unique_frames(video, capped, cap=1)) == 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_frames_text_ocrs_each_unique_frame(settings, monkeypatch):
    video = _two_scene_video(settings)
    settings.ocr_backend = "local"
    texts = iter(["SLIDE ONE", "SLIDE TWO"])
    monkeypatch.setattr(
        "sift.extract.images_ocr._run_local", lambda s, b, m: next(texts)
    )
    assert reels_stt._frames_text(video, settings) == "SLIDE ONE\n\nSLIDE TWO"


def test_router_dispatches_reel_and_video(settings, monkeypatch):
    marker = object()
    monkeypatch.setattr("sift.extract.reels_stt.extract", lambda i, s: marker)
    for medium in (Medium.reel, Medium.video):
        assert router.extract(_item(medium=medium), settings) is marker
