"""Reels / video speech-to-text via faster-whisper. DESIGN.md §7; BUILD_CHECKLIST F4.

faster-whisper decodes video containers (mp4/mov) directly through PyAV, so there is no
ffmpeg subprocess step — the design's "ffmpeg -> STT" intent (audio out of video) happens
inside the library. Language is detected and recorded with transcription confidence;
low-confidence transcripts are flagged (logged + artifacts), never dropped (CLAUDE.md
Conventions). Translation only ever targets English (Whisper's only translate direction,
DESIGN §7) and is off by default.

The transcript is untrusted text and re-enters the same sanitize -> interpreter path as
any caption. Heavy deps live in the [stt] extra; a missing install or a per-file failure
is logged and the item still gets its caption-only note.
"""

from __future__ import annotations

import logging
import math

from ..config import Settings
from ..models import Extraction, Item

logger = logging.getLogger(__name__)

# Containers PyAV can pull an audio stream from: reels are mp4; self-DM voice notes m4a.
_MEDIA_SUFFIXES = {".mp4", ".mov", ".m4a", ".webm", ".mkv"}

# Below this mean segment probability the transcript is flagged low-confidence.
_LOW_CONFIDENCE = 0.5

_MODELS: dict[str, object] = (
    {}
)  # model-name -> loaded WhisperModel (load once per process)


def _transcribe(path: str, settings: Settings) -> tuple[str, str, float]:
    """One file -> (text, language, confidence). The model seam offline tests monkeypatch.

    Imports faster-whisper lazily: it lives in the [stt] extra (pyproject).
    """
    from faster_whisper import WhisperModel

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
    return Extraction(
        text="\n\n".join(texts),
        languages=languages,
        # Conservative: the weakest file's confidence represents the item.
        confidence=min(confidences) if confidences else None,
        artifacts={"low_confidence": low_confidence} if low_confidence else {},
    )
