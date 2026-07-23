"""Adapter: weekly local cookie-based fetch of the user's OWN Saved folder. DESIGN.md §4.

RUNS ON THE USER'S MACHINE + HOME IP. READ-ONLY. Never like/follow/comment/DM.
Weekly default (daily ceiling, NEVER hourly). One session. Back off (pause days) on HTTP 429.
No instagrapi — this uses the user's real logged-in web session via gallery-dl.
"""

from __future__ import annotations

from ..config import Settings
from ..models import Item, RunReport


def pull(
    settings: Settings, path: str | None = None, report: RunReport | None = None
) -> list[Item]:
    """Incremental gallery-dl fetch of the Saved folder -> Items (with captions).

    TODO F2:
      - gallery-dl: instagram 'saved' extractor, --cookies-from-browser {settings.cookie_browser}
      - metadata postprocessor -> caption (the 'description' field)
      - --download-archive for incremental (only new items)
      - sleep-request >= 3s ; single persistent session ; exponential back-off on 429
      - Playwright fallback if web GraphQL shifts
      - ingest_via=cookie_fetch
    """
    raise NotImplementedError("F2: cookie-based saved-folder fetch")
