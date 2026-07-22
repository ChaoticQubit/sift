"""Caption = first-class content (do this FIRST). DESIGN.md §7.

Captions arrive already on the Item (from gallery-dl 'description' / share JSON / export),
so this mostly normalizes/cleans and flags empties — no OCR/STT needed. Highest signal, cheapest.
"""
from __future__ import annotations

from ..models import Extraction, Item


def extract(item: Item) -> Extraction:
    """Return the caption as an Extraction (cleaned). TODO F3: normalize, language-detect."""
    return Extraction(text=item.caption or "")
