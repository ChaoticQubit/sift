"""Readable-content extraction for shared links via trafilatura (NOT Mercury — dead). DESIGN.md §7.

Credential-less fetch + private-IP block via guardrails.egress (AGENTS.md §3a); the
response body is untrusted input. A refused, failed, or unreadable fetch is logged and
yields an empty Extraction — the caption-only note still gets written, never a dropped item.
"""

from __future__ import annotations

import logging

import trafilatura

from ..config import Settings
from ..guardrails.egress import EgressError, guarded_fetch
from ..models import Extraction, Item

logger = logging.getLogger(__name__)

# ponytail: parse cap, not a streaming cap — urllib3 preloads the body; this only
# shields trafilatura from a pathological page. Stream + abort if memory ever bites.
_MAX_BYTES = 5_000_000


def extract(item: Item, settings: Settings) -> Extraction:
    url = item.permalink
    if not url:
        logger.warning("links: item %s has no permalink; nothing to fetch", item.id)
        return Extraction()
    try:
        response = guarded_fetch(url)
    except EgressError as exc:
        logger.warning("links: fetch refused for %s (%s); caption-only", url, exc)
        return Extraction()
    except Exception as exc:  # DNS/TLS/timeout — network failure, not policy
        logger.warning("links: fetch failed for %s (%s); caption-only", url, exc)
        return Extraction()
    if response.status != 200:
        logger.warning("links: HTTP %s for %s; caption-only", response.status, url)
        return Extraction()
    html = response.data[:_MAX_BYTES].decode("utf-8", errors="replace")
    text = trafilatura.extract(html, url=url, include_comments=False) or ""
    meta = trafilatura.extract_metadata(html)
    title = (meta.title or "") if meta else ""
    if not (text or title):
        logger.info("links: no readable content at %s; caption-only", url)
    return Extraction(
        text="\n\n".join(t for t in (title, text) if t),
        artifacts={"url": url, "title": title} if title else {"url": url},
    )
