"""Shared offline fixtures. No network, no API keys, no real vault."""

from __future__ import annotations

import pytest

from sift.config import Settings


@pytest.fixture
def settings(tmp_path):
    # Explicit init values beat any ambient SIFT_* env vars (pydantic-settings priority).
    return Settings(vault_path=tmp_path / "vault", work_dir=None)
