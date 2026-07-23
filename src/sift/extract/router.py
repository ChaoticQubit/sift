"""Dispatch an Item to the right extractor by medium (DESIGN.md §7)."""

from __future__ import annotations

from ..config import Settings
from ..models import Extraction, Item, Medium


def extract(item: Item, settings: Settings) -> Extraction:
    if item.medium in (Medium.carousel, Medium.image):
        from . import images_ocr

        return images_ocr.extract(item, settings)
    if item.medium in (Medium.reel, Medium.video):
        from . import reels_stt

        return reels_stt.extract(item, settings)
    if item.medium == Medium.link:
        from . import links

        return links.extract(item, settings)
    from . import captions

    return captions.extract(item)
