"""Adapter: official 'Download Your Information' export ZIP. Zero ban risk. DESIGN.md §4.

Backfill + the ONLY safe route to already-sent self-DMs. Extracts captions too.
"""

from __future__ import annotations

from ..config import Settings
from ..models import Item


def pull(settings: Settings, path: str | None = None) -> list[Item]:
    """Parse the DYI ZIP -> Items (saved posts, saved collections, self-DM thread, captions).

    TODO F1:
      - your_instagram_activity/saved/saved_posts.json (permalinks + timestamps)
      - saved collections (names + contents)
      - messages/inbox/<self>/message_*.json (shared posts + captions)
      - Item.id = hash(permalink) + saved_at ; ingest_via=dyi_export
    """
    raise NotImplementedError("F1: parse DYI export")
