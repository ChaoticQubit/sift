"""Readable-content extraction for shared links via trafilatura (NOT Mercury — dead). DESIGN.md §7."""
from __future__ import annotations

from ..config import Settings
from ..models import Extraction, Item


def extract(item: Item, settings: Settings) -> Extraction:
    """TODO F2: resolve URL -> trafilatura -> title + readable text + metadata."""
    raise NotImplementedError("F2: link extraction")
