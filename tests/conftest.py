"""Shared offline fixtures. No network, no API keys, no real vault."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sift.config import Settings
from sift.models import Interpretation


@pytest.fixture
def settings(tmp_path):
    # Explicit init values beat any ambient SIFT_* env vars (pydantic-settings priority).
    return Settings(vault_path=tmp_path / "vault", work_dir=None)


@pytest.fixture
def fake_model(monkeypatch):
    """Mock the interpreter's model-call seam (offline rule — CLAUDE.md Conventions).

    Records every model-bound prompt; returns `.result` as the run output content
    (set it to an Exception instance to make the run raise instead).
    """
    seam = SimpleNamespace(prompts=[], result=Interpretation())

    def _fake_run(agent, prompt):
        seam.prompts.append(prompt)
        if isinstance(seam.result, Exception):
            raise seam.result
        return SimpleNamespace(content=seam.result)

    monkeypatch.setattr("sift.agents.interpreter._run", _fake_run)
    return seam
