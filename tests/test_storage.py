"""Dedup ledger roundtrip — offline, tmp dirs only."""

from __future__ import annotations

from sift.db import storage


def test_ledger_roundtrip(settings):
    assert storage.is_new("item-1", settings)
    storage.mark_seen("item-1", settings)
    assert not storage.is_new("item-1", settings)
    assert storage.is_new("item-2", settings)
    # Ledger lives under resolved_work_dir (<vault>/.sift), created lazily.
    assert settings.resolved_work_dir == settings.vault_path / ".sift"
    assert (settings.resolved_work_dir / "ledger.db").is_file()


def test_mark_seen_idempotent(settings):
    storage.mark_seen("dup", settings)
    storage.mark_seen("dup", settings)
    assert not storage.is_new("dup", settings)
