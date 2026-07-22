"""Compose reader + enricher as an Agno Team/Workflow. AGENTS.md §1-2.

Deterministic step pipeline (Workflow) is preferred so guardrails + Rule-of-Two boundary are explicit.
"""
from __future__ import annotations

# TODO F3: assemble the ingestion/enrichment pipeline as an Agno Workflow with the
# quarantined reader -> privileged enricher boundary and shared SQLite storage.
