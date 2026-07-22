"""Fetcher egress policy — split by trust (AGENTS.md §3a; decided 2026-07-22).

The interpreter agent has NO tools, so agent-context egress is only the model API call.
This module governs the deterministic fetchers:
  - gallery-dl / yt-dlp: scoped to instagram.com + its CDNs (cdninstagram.com, fbcdn.net);
    session cookies allowed — that is their job.
  - link fetcher (trafilatura): any public URL, but CREDENTIAL-LESS (never send cookies/auth)
    and private/loopback/link-local addresses BLOCKED (SSRF guard). Response = untrusted.
"""
from __future__ import annotations

# TODO F0.5: is_fetchable(url) -> bool (public-IP check after DNS resolution), plus the
# credential-less requests session the link extractor must use.
