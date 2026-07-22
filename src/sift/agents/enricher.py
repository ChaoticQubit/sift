"""PRIVILEGED enricher agent (Agno). Acts ONLY on sanitized structured data. AGENTS.md §2.

Never sees raw caption text as instructions. reasoning=True. Write tools gated with
requires_confirmation. post_hooks = [SensitiveOutputGuardrail] (LLM Guard).
"""
from __future__ import annotations

from ..config import Settings
from ..models import Extraction, Item, Note


def enrich(item: Item, extraction: Extraction, settings: Settings) -> Note:
    """Classify + summarize + candidate action items (strict schema) on sanitized data -> Note.

    TODO F3: build the Agno Agent (reasoning on), strict Pydantic output; entertainment -> one-liner.
    """
    raise NotImplementedError("F3: privileged enricher agent")
