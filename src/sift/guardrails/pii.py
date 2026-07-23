"""PII masking for everything model-API-bound. AGENTS.md §3a (Presidio + LLM Guard cut 2026-07-22).

Agno's built-in PIIDetectionGuardrail(mask=True) is the hook; the same masking is exposed
as a plain callable so the F5 RAG embed path can reuse it outside an agent context.
The vault always stays verbatim.
"""

from __future__ import annotations

# TODO F0.5: configure PIIDetectionGuardrail(mask=True) for the interpreter's pre_hooks,
# and expose mask_for_model(text: str) -> str for the embedder path (F5).
