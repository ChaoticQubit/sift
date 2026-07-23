"""OCR every image in a carousel (loop all N) via a vision-LLM. DESIGN.md §7 + §13.3.

VLM-only (decided 2026-07-22): Instagram content is out-of-domain for document OCR engines
and home turf for a VLM. The call is TOOL-LESS and its text output re-enters the same
sanitize -> interpreter path as any untrusted text. Ships image bytes to the provider
(accepted tradeoff); a local OCR engine/VLM is the documented swap.
"""

from __future__ import annotations

from ..config import Settings
from ..models import Extraction, Item


def extract(item: Item, settings: Settings) -> Extraction:
    """TODO F3: for each media file -> tool-less VLM OCR call -> concat text per image."""
    raise NotImplementedError("F3: carousel/image OCR (VLM)")
