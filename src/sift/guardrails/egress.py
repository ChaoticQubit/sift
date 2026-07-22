"""Egress allowlist — the control that actually stops exfiltration. AGENTS.md §3a."""
from __future__ import annotations

# TODO F0.5: route outbound HTTP through a proxy restricted to instagram.com + own API.
# No long-lived secrets in agent context; short-lived audience-scoped tokens only.
