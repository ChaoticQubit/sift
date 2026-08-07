"""Collect candidate action items from notes; dues are already typed dates. DESIGN.md §9.

Candidates come out of note frontmatter — the schema-validated, value-re-forced records
the writer produced. No raw-text date parsing (BUILD_CHECKLIST F6): `due` arrives as a
date or not at all. A malformed frontmatter entry (hand-edit gone wrong) is logged and
skipped, never fatal.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import frontmatter

from ..config import Settings
from ..models import ActionItem

logger = logging.getLogger(__name__)

# Capture-note filenames end in the 8-hex Item.id hash (vault/writer.py) — the stable
# key that survives slug changes; also the digest's line-marker key.
_NOTE_KEY = re.compile(r"-([0-9a-f]{8})$")


@dataclass
class Candidate:
    note: Path  # source note path
    key: str  # 8-hex id-hash from the filename
    index: int  # position in the note's action_items list
    item: ActionItem
    domain: str = "misc"  # note's models.Domain shelf (frontmatter)
    # When the post was SAVED on the platform. Deliberately not `created`: that is
    # the note's (re)write date, which reprocessing resets — it says nothing about
    # whether the content is still current.
    saved_at: date | None = None


def note_key(path: Path) -> str | None:
    m = _NOTE_KEY.search(path.stem)
    return m.group(1) if m else None


def collect(settings: Settings) -> list[Candidate]:
    """Gather candidates across all capture notes."""
    out: list[Candidate] = []
    for path in sorted((settings.vault_path / "Captures").glob("*.md")):
        key = note_key(path)
        if key is None:
            continue  # not a sift capture note
        post = frontmatter.load(path)
        saved = post.metadata.get("saved_at")
        for i, raw in enumerate(post.metadata.get("action_items") or []):
            try:
                item = ActionItem.model_validate(raw)
            except Exception as exc:
                logger.warning(
                    "action_items: %s[%d] malformed (%s); skipped", path.name, i, exc
                )
                continue
            out.append(
                Candidate(
                    note=path,
                    key=key,
                    index=i,
                    item=item,
                    domain=str(post.metadata.get("domain") or "misc"),
                    saved_at=saved if isinstance(saved, date) else None,
                )
            )
    return out
