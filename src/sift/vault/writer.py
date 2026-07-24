"""Idempotent markdown note writer — deterministic code, never an agent tool. DESIGN.md §6; CLAUDE.md rules 4-5.

The caption in the note comes VERBATIM from the Item (PII masking is LLM-bound only, AGENTS.md §3a).
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import frontmatter

from ..config import Settings
from ..db import storage
from ..models import Classification, Item, Note
from .frontmatter import to_frontmatter

logger = logging.getLogger(__name__)

# Characters that could turn model-produced text into live markdown in Obsidian.
_MD_SPECIALS = re.compile(r"([\\`*_{}\[\]()<>#+!|])")


def _escape_md(text: str) -> str:
    """Render model-produced text (summary, action text) as inert plain prose."""
    return _MD_SPECIALS.sub(r"\\\1", text)


def _fence(text: str) -> str:
    """Fenced code block whose fence is always longer than any backtick run inside.

    Fencing preserves every byte while defanging rendering (DESIGN.md §6): an embedded
    ![](http://attacker/pixel.png) stays text, never a tracking beacon.
    """
    longest = max((len(m.group()) for m in re.finditer(r"`+", text)), default=0)
    tick = "`" * max(3, longest + 1)
    return f"{tick}\n{text}\n{tick}"


def _slug(note: Note) -> str:
    base = note.item.caption or note.interpretation.summary or note.item.medium.value
    first_line = (base.splitlines() or [""])[0]
    slug = re.sub(r"[^a-z0-9]+", "-", first_line.lower()).strip("-")[:60].strip("-")
    return slug or "capture"


def _one_liner(note: Note) -> str:
    """Entertainment routing: a single log-entry line instead of a full note.

    Never caption-derived: untrusted caption text appears only inside fenced blocks
    (DESIGN.md §6) — an unfenced bare URL would auto-link in Obsidian's reading view.
    """
    text = note.interpretation.summary or "entertainment"
    line = _escape_md((text.splitlines() or [""])[0])
    if note.item.permalink:
        line += f" — {note.item.permalink}"
    return line


def write(note: Note, settings: Settings) -> str:
    """Write / refresh a capture note in <vault>/Captures. Idempotent by Item.id.

    entertainment -> one-line log entry only. Returns the note path.
    Caption/extracted bodies go in FENCED code blocks (render-defanged — DESIGN.md §6);
    model-produced fields (summary/tags/action text) are escaped plain text; action-item
    status is re-forced to "pending" here regardless of what the record claims.
    """
    # The model proposes; deterministic code disposes (AGENTS.md §2).
    for ai in note.interpretation.action_items:
        ai.status = "pending"

    captures = settings.vault_path / "Captures"
    captures.mkdir(parents=True, exist_ok=True)
    short = hashlib.sha256(note.item.id.encode()).hexdigest()[:8]
    # Identity lives in the id-hash suffix, not the content-derived slug: refresh the
    # existing note for this Item.id even if the caption/summary (and thus slug) changed.
    existing = sorted(captures.glob(f"*-{short}.md"))
    path = existing[0] if existing else captures / f"{_slug(note)}-{short}.md"

    if note.interpretation.classification is Classification.entertainment:
        logger.info("entertainment -> one-liner: %s", note.item.id)
        body = _one_liner(note)
    else:
        parts: list[str] = []
        if note.interpretation.summary:
            parts.append(_escape_md(note.interpretation.summary))
        if note.item.caption:
            parts.append("## Caption\n\n" + _fence(note.item.caption))
        if note.extraction.text and note.extraction.text != note.item.caption:
            parts.append("## Extracted\n\n" + _fence(note.extraction.text))
        parts.extend(f"![[{media}]]" for media in note.item.media_files)
        body = "\n\n".join(parts)

    post = frontmatter.Post(body, **to_frontmatter(note))
    path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")
    storage.mark_seen(note.item.id, settings)
    return str(path)


def merge_source(path: str, item: Item, settings: Settings) -> str:
    """Fold a near-duplicate capture into its EXISTING note (one page per piece of
    content — rag/link.py decides sameness). Records the extra permalink in a
    `sources` frontmatter list and the caption when it differs; marks the dupe's
    Item.id seen so reruns skip it. Idempotent."""
    p = Path(path)
    post = frontmatter.load(p)
    if item.permalink and item.permalink != post.metadata.get("source"):
        sources = [str(s) for s in post.metadata.get("sources") or []]
        if item.permalink not in sources:
            post.metadata["sources"] = sources + [item.permalink]
    if (
        item.caption
        and item.caption != post.metadata.get("caption")
        and item.caption not in post.content
    ):
        post.content += "\n\n## Caption (duplicate save)\n\n" + _fence(item.caption)
    p.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")
    storage.mark_seen(item.id, settings)
    return str(p)
