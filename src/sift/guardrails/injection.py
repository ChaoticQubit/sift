"""Untrusted-content hardening for captions/links. AGENTS.md §3, §5.

Blocking on injection patterns is a silent data-loss mode (saved posts legitimately quote
injection phrasing); the structural quarantine already makes payloads inert, so pattern
detection is LOG-ONLY.
"""

from __future__ import annotations

# TODO F0.5:
#   UntrustedContentGuardrail -> spotlight/delimit caption as DATA (randomized delimiters),
#     strip Unicode tag chars U+E0000..U+E007F, sanitize markdown image/link syntax,
#     normalize whitespace. The sanitize is a plain function usable outside hooks too.
#   InjectionFlagGuardrail -> annotate known injection patterns for observability; NEVER raises.
