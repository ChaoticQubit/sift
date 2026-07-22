"""QUARANTINED interpreter agent (Agno) — the ONLY model that reads untrusted text. AGENTS.md §2.

NO tools. NO secrets in context. Input: sanitized caption/OCR/transcript/link text.
Output: strict `Interpretation` record (classification, summary, tags, action candidates).
Off-schema output is rejected and logged. A hijacked interpreter has no levers to pull.
pre_hooks  = [PromptInjectionGuardrail, PIIDetectionGuardrail(mask=True), PresidioGuardrail,
              UntrustedContentGuardrail]
post_hooks = [SensitiveOutputGuardrail]
"""
from __future__ import annotations

from ..config import Settings
from ..models import Extraction, Interpretation, Item


def interpret(item: Item, extraction: Extraction, settings: Settings) -> Interpretation:
    """Run the quarantined interpreter over sanitized text -> validated Interpretation.

    TODO F0.5: agent skeleton (tool-less, output_schema=Interpretation, guardrail hooks).
    TODO F3: full prompt (spotlighted untrusted DATA), entertainment short-circuit handled
    downstream by the writer, schema-validation rejection path logged to RunReport.
    """
    raise NotImplementedError("F0.5/F3: quarantined interpreter agent")
