"""Wire the interpreter's pre_hooks/post_hooks. Order cheap->expensive. AGENTS.md §3."""
from __future__ import annotations

# TODO F0.5:
#   INTERPRETER pre_hooks  = [PromptInjectionGuardrail(), PIIDetectionGuardrail(mask=True),
#                             PresidioGuardrail(), UntrustedContentGuardrail()]
#   INTERPRETER post_hooks = [SensitiveOutputGuardrail()]   # LLM Guard Sensitive + MaliciousURLs
