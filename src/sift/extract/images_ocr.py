"""OCR every image in a carousel (loop all N). DESIGN.md §7 + §13 caveats.

PaddleOCR default; keep a vision-LLM fallback for stylized/garbled images.
Instagram images are OUT OF DOMAIN for document OCR — validate on real saved images.
"""
from __future__ import annotations

from ..config import Settings
from ..models import Extraction, Item


def extract(item: Item, settings: Settings) -> Extraction:
    """TODO F3: for each media file -> OCR -> concat raw + cleaned text; VLM fallback hook."""
    raise NotImplementedError("F3: carousel/image OCR")
