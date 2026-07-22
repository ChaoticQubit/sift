"""Collect candidate action items from notes; normalize dates. DESIGN.md §9."""
from __future__ import annotations

from ..config import Settings
from ..models import ActionItem


def collect(settings: Settings) -> list[ActionItem]:
    """TODO F5: gather candidates across notes; dateparser-normalize dues; score confidence."""
    raise NotImplementedError("F5: collect action items")
