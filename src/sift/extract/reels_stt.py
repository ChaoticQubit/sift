"""Reels / video speech-to-text via faster-whisper + on-screen text via frame OCR.

DESIGN.md §7; BUILD_CHECKLIST F4. faster-whisper decodes video containers (mp4/mov)
directly through PyAV — the design's "ffmpeg -> STT" intent (audio out of video)
happens inside the library. Language is detected and recorded with transcription
confidence; low-confidence transcripts are flagged (logged + artifacts), never
dropped (CLAUDE.md Conventions). Translation only ever targets English (Whisper's
only translate direction, DESIGN §7) and is off by default.

A transcript alone loses everything shown on screen (book lists, slides, overlays —
often the whole point of a reel set to music). So each video also gets frame OCR:
ffmpeg's scene-change filter keeps only the UNIQUE frames (first frame + every
visual change), a dynamic duration-scaled cap bounds pathological slideshows, and
the frames run through images_ocr's shared engine. Frames are temp files — analysis
input only, never vault content. Note the cost model: with the "local" OCR backend
frames are free; with the "vlm" backend each unique frame is one provider call.

Transcript and frame text are both untrusted and re-enter the same sanitize ->
interpreter path as any caption. Heavy deps live in the [stt] extra; a missing
install or a per-file failure is logged and the item still gets its caption-only
note.
"""

from __future__ import annotations

import logging
import math
import subprocess
import tempfile
from pathlib import Path

from ..config import Settings
from ..models import Extraction, Item
from . import images_ocr

logger = logging.getLogger(__name__)

# Containers PyAV can pull an audio stream from: reels are mp4; self-DM voice notes m4a.
_MEDIA_SUFFIXES = {".mp4", ".mov", ".m4a", ".webm", ".mkv"}

# Below this mean segment probability the transcript is flagged low-confidence.
_LOW_CONFIDENCE = 0.5

_MODELS: dict[str, object] = (
    {}
)  # model-name -> loaded WhisperModel (load once per process)

# Fraction of visual change (0..1) that makes a frame "unique" for frame OCR.
_SCENE_THRESHOLD = 0.4


def _frame_cap(seconds: float) -> int:
    """Duration-scaled frame budget — longer videos get more unique frames so a
    dense lecture loses nothing to a one-size cap. Unknown duration (0) is
    treated as short."""
    if seconds < 60:
        return 20
    if seconds <= 300:
        return 50
    return 100


def _unique_frames(path: str, out_dir: Path, cap: int) -> list[Path]:
    """First frame + every scene change: ffmpeg's select filter IS the
    similar-frame dedup — consecutive near-identical frames never come out."""
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-i",
            path,
            "-vf",
            f"select='eq(n,0)+gt(scene,{_SCENE_THRESHOLD})'",
            "-fps_mode",
            "vfr",
            "-frames:v",
            str(cap),
            str(out_dir / "frame_%03d.png"),
        ],
        check=True,
        capture_output=True,
        timeout=600,
    )
    return sorted(out_dir.glob("frame_*.png"))


def _frames_text(path: str, settings: Settings) -> str:
    """On-screen text for one video: unique frames -> shared OCR engine.
    Frames live in a temp dir reclaimed before returning."""
    import av

    with av.open(path) as container:
        if not container.streams.video:
            return ""  # voice note / audio-only file
        # container.duration is in AV_TIME_BASE (microsecond) units; av.time_base
        # is the int 1_000_000, so DIVIDE — multiplying made every video look
        # hours long and always hit the max frame cap.
        seconds = (container.duration or 0) / av.time_base
    with tempfile.TemporaryDirectory() as tmp:
        frames = _unique_frames(path, Path(tmp), _frame_cap(seconds))
        logger.info("reels_stt: %s -> %d unique frame(s) for OCR", path, len(frames))
        return "\n\n".join(images_ocr.ocr_paths(frames, settings))


def _transcribe(path: str, settings: Settings) -> tuple[str, str, float]:
    """One file -> (text, language, confidence). The model seam offline tests monkeypatch.

    Imports faster-whisper lazily: it lives in the [stt] extra (pyproject).
    """
    import av
    from faster_whisper import WhisperModel

    # A soundless video (reel with removed audio) crashes PyAV's stream lookup
    # inside faster-whisper with a bare IndexError; probe first and say why.
    with av.open(path) as container:
        if not container.streams.audio:
            logger.info("reels_stt: %s has no audio stream; nothing to transcribe", path)
            return "", "", 1.0

    model = _MODELS.get(settings.stt_model)
    if model is None:
        # ponytail: int8 on CPU — ctranslate2 has no MPS backend; revisit if a Metal
        # build ever lands.
        model = WhisperModel(settings.stt_model, compute_type="int8")
        _MODELS[settings.stt_model] = model
    segments, info = model.transcribe(  # type: ignore[attr-defined]
        path,
        task="translate" if settings.stt_translate_to_english else "transcribe",
    )
    texts: list[str] = []
    probs: list[float] = []
    for segment in segments:
        texts.append(segment.text.strip())
        probs.append(math.exp(segment.avg_logprob))
    confidence = sum(probs) / len(probs) if probs else 0.0
    return " ".join(t for t in texts if t), info.language, confidence


def extract(item: Item, settings: Settings) -> Extraction:
    """Loop all media files -> transcribe each -> concatenated transcript."""
    texts: list[str] = []
    languages: list[str] = []
    confidences: list[float] = []
    low_confidence: list[str] = []
    for name in item.media_files:
        path = settings.vault_path / name
        if path.suffix.lower() not in _MEDIA_SUFFIXES:
            logger.info("reels_stt: %s is not a transcribable container; skipped", name)
            continue
        if not path.is_file():
            logger.warning("reels_stt: %s missing from vault; skipped", name)
            continue
        try:
            text, language, confidence = _transcribe(str(path), settings)
        except ImportError:
            logger.warning(
                "reels_stt: faster-whisper not installed (pip install 'sift[stt]'); "
                "caption-only for %s",
                item.id,
            )
            return Extraction()
        except Exception as exc:
            logger.warning("reels_stt: failed on %s (%s); file skipped", name, exc)
            continue
        if confidence < _LOW_CONFIDENCE:
            low_confidence.append(name)
            logger.warning(
                "reels_stt: LOW-CONFIDENCE transcript for %s (%.2f, lang=%s) — kept and flagged",
                name,
                confidence,
                language,
            )
        if text:
            texts.append(text)
            if language not in languages:
                languages.append(language)
            confidences.append(confidence)
        try:
            frame_text = _frames_text(str(path), settings)
        except Exception as exc:
            logger.warning(
                "reels_stt: frame OCR failed on %s (%s); transcript-only", name, exc
            )
            frame_text = ""
        if frame_text:
            texts.append(frame_text)
    return Extraction(
        text="\n\n".join(texts),
        languages=languages,
        # Conservative: the weakest file's confidence represents the item.
        confidence=min(confidences) if confidences else None,
        artifacts={"low_confidence": low_confidence} if low_confidence else {},
    )
