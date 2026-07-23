"""PII masking for everything model-API-bound. AGENTS.md §3a (Presidio + LLM Guard cut 2026-07-22).

Agno's built-in PIIDetectionGuardrail (real kwarg: mask_pii=True, not the mask=True the
docs sketched) is the hook; `mask_for_model` applies the SAME compiled patterns as a plain
callable so the F5 RAG embed path can reuse it outside an agent context.
The vault always stays verbatim — masking applies only to model-API-bound copies.
"""

from __future__ import annotations

import re

from agno.guardrails import PIIDetectionGuardrail

# The built-in covers SSN / credit card / email / phone; IP addresses added here.
# ponytail: IPv4 only — add an IPv6 literal pattern if one ever shows up in captions.
_IP_ADDRESS = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# Single source of truth for patterns AND the hook instance: the hook masks in-place on
# the agent's RunInput; mask_for_model runs the identical substitution on plain text.
_DETECTOR = PIIDetectionGuardrail(
    mask_pii=True, custom_patterns={"IP Address": _IP_ADDRESS}
)


def mask_for_model(text: str) -> str:
    """Mask PII in a model-API-bound copy of text. The F5 embed path reuses this."""
    for pattern in _DETECTOR.pii_patterns.values():
        text = pattern.sub(lambda m: "*" * len(m.group(0)), text)
    return text


def pii_guardrail() -> PIIDetectionGuardrail:
    """The hook form for the interpreter's pre_hooks (same patterns as mask_for_model)."""
    return _DETECTOR
