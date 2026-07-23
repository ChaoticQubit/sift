"""Collect candidate action items from notes; normalize dates. DESIGN.md §9."""
from __future__ import annotations

from ..config import Settings
from ..models import ActionItem


def collect(settings: Settings) -> list[ActionItem]:
    """TODO F6: gather candidates across notes (dues already typed dates via the schema)."""
    raise NotImplementedError("F6: collect action items")
