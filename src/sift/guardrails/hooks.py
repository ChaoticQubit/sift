"""Wire the pre_hooks/post_hooks for reader/enricher. Order cheap->expensive. AGENTS.md §3."""
from __future__ import annotations

# TODO F0.5:
#   READER pre_hooks  = [PromptInjectionGuardrail(), PIIDetectionGuardrail(mask=True),
#                        PresidioGuardrail(), UntrustedContentGuardrail()]
#   ENRICHER post_hooks = [SensitiveOutputGuardrail()]   # LLM Guard Sensitive + MaliciousURLs
