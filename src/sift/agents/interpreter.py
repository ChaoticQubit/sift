"""QUARANTINED interpreter agent (Agno) — the only agent in the system. AGENTS.md §2.

NO tools. NO secrets in context. NO session memory — stateless per item (persisted memory
would carry a poisoned item's content into every later context).
Input: sanitized caption/OCR/transcript/link text. Output: strict `Interpretation` record.
Off-schema output is rejected and logged. Boundary code re-forces security-relevant values
(status -> pending, confidence clamped) — the model proposes, deterministic code disposes.
pre_hooks = [PIIDetectionGuardrail(mask=True), UntrustedContentGuardrail, InjectionFlagGuardrail (log-only)]
"""

from __future__ import annotations

from ..config import Settings
from ..models import Extraction, Interpretation, Item


def interpret(item: Item, extraction: Extraction, settings: Settings) -> Interpretation:
    """Run the quarantined interpreter over sanitized text -> validated Interpretation.

    TODO F0.5: agent skeleton (tool-less, stateless, output_schema=Interpretation, hooks inline).
    TODO F3: full prompt (spotlighted untrusted DATA); entertainment short-circuit handled
    downstream by the writer; schema-rejection path logged to RunReport.
    """
    raise NotImplementedError("F0.5/F3: quarantined interpreter agent")
