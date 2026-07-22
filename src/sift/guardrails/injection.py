"""Prompt-injection + untrusted-content hardening for captions/links. AGENTS.md §3, §5."""
from __future__ import annotations

# TODO F0.5: UntrustedContentGuardrail -> spotlight/delimit caption as DATA, strip Unicode tag
# chars U+E0000..U+E007F, sanitize markdown image/link syntax. Plus Agno PromptInjectionGuardrail.
