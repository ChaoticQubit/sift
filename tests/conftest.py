"""Shared offline fixtures. No network, no API keys, no real vault."""

from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from sift.config import Settings
from sift.models import Interpretation

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings(tmp_path):
    # Explicit init values beat any ambient SIFT_* env vars (pydantic-settings priority).
    return Settings(vault_path=tmp_path / "vault", work_dir=None)


@pytest.fixture
def dyi_zip(tmp_path):
    """The checked-in fake DYI export assembled into a real ZIP (offline, synthetic).

    Adds one present media file at zip time (no binary files in the repo —
    fixtures/README.md); photo_missing.jpg is referenced but deliberately absent.
    """
    zip_path = tmp_path / "instagram-fake-2026-07-20.zip"
    base = FIXTURES / "your_instagram_activity"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in sorted(base.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(FIXTURES).as_posix())
        zf.writestr(
            "your_instagram_activity/messages/inbox/self_notes/photos/photo_present.jpg",
            b"\xff\xd8\xff\xe0fake-jpeg-bytes",
        )
    return zip_path


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
