"""Speech-to-text for reels/videos via freeflow / faster-whisper. DESIGN.md §7 + §13.

Major-language target in v1. Detect language, record confidence, flag low-confidence.
Whisper translate is X->English only; non-English targets are DEFERRED.
"""
from __future__ import annotations

from ..config import Settings
from ..models import Extraction, Item


def extract(item: Item, settings: Settings) -> Extraction:
    """TODO F4: ffmpeg extract audio -> STT -> transcript + languages + confidence."""
    raise NotImplementedError("F4: reel/video STT")
