"""Readable-content extraction for shared links via trafilatura (NOT Mercury — dead). DESIGN.md §7."""

from __future__ import annotations

from ..config import Settings
from ..models import Extraction, Item


def extract(item: Item, settings: Settings) -> Extraction:
    """TODO F3: resolve URL -> trafilatura -> title + readable text + metadata.

    Credential-less fetch + private-IP block via guardrails.egress (AGENTS.md §3a).
    """
    raise NotImplementedError("F3: link extraction")
