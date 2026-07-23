"""Hello-world Agno agent factory — proves the framework + v2 pin (F0).

Construction only, NO API call. This is NOT the interpreter (that quarantined,
tool-less agent is agents/interpreter.py, F0.5/F3 — AGENTS.md §2).
"""

from __future__ import annotations

from agno.agent import Agent
from agno.models.anthropic import Claude

from ..config import Settings


def build(settings: Settings) -> Agent:
    """Construct a minimal tool-less Agno agent on the configured anthropic model."""
    return Agent(name="sift-hello", model=Claude(id=settings.llm_model))
