"""Adapter: capture-at-save-time via Share-Sheet / Shortcut. Zero ban risk, real-time. DESIGN.md §4.

Replaces the 'DM myself' habit: user taps Share -> Sift, item lands instantly. Strategic end state.
"""
from __future__ import annotations

from ..config import Settings
from ..models import Item


def pull(settings: Settings, path: str | None = None) -> list[Item]:
    """Drain the capture endpoint's inbox -> Items (with captions). ingest_via=share_capture.

    TODO F7: a small local endpoint receives shared posts + typed notes from an iOS/Android
    Share-Sheet/Shortcut + browser action; normalize into Items.
    """
    raise NotImplementedError("F7: share-sheet capture")
