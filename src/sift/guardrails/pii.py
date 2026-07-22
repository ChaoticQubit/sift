"""PII guardrails. Agno PIIDetectionGuardrail(mask=True) + Presidio (real recall) + LLM Guard output. AGENTS.md §3a."""
from __future__ import annotations

# TODO F0.5: PresidioGuardrail(BaseGuardrail) wrapping AnalyzerEngine + AnonymizerEngine
# (add PatternRecognizers for app-specific IDs). SensitiveOutputGuardrail wrapping llm_guard Sensitive.
