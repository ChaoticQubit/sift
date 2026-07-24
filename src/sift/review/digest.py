"""Write / refresh Action Review.md — the human approval surface. DESIGN.md §9.

NEVER fires alerts autonomously (CLAUDE.md rule 7); items stay `status: pending` until
the captain confirms. The digest is both the render AND the editing surface: refresh()
first applies the checkbox states the user set in Obsidian back into the source notes'
frontmatter — `[ ]` pending, `[x]` confirmed, `[-]` dismissed (Obsidian's cancelled
state) — then re-renders from the notes. Dismissed items leave the digest but keep
their status in the note; confirmed items stay visible under Confirmed (they become
reminders only at the Life Planner fold-in, behind its own approve-gate).

Every digest line ends in a `^sift-<notekey>-<index>` block reference: Obsidian-native,
stable across slug changes, and the apply parser's anchor.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

import frontmatter

from ..config import Settings
from ..vault.writer import _escape_md
from .action_items import Candidate, collect, note_key

logger = logging.getLogger(__name__)

_MARKER = re.compile(r"^- \[([ x-])\] .*\^sift-([0-9a-f]{8})-(\d+)\s*$")
_STATUS = {" ": "pending", "x": "confirmed", "-": "dismissed"}

_HEADER = (
    "Draft-then-approve (DESIGN §9): check `[x]` to confirm, mark `[-]` to dismiss; "
    "the next `sift run` or `sift review` syncs statuses into the source notes. "
    "Nothing fires unconfirmed."
)


def _apply(digest_path: Path, settings: Settings) -> None:
    """Sync the user's checkbox edits back into note frontmatter."""
    if not digest_path.exists():
        return
    edits: dict[tuple[str, int], str] = {}
    for line in digest_path.read_text(encoding="utf-8").splitlines():
        m = _MARKER.match(line)
        if m:
            edits[(m.group(2), int(m.group(3)))] = _STATUS[m.group(1)]
    if not edits:
        return
    notes = {note_key(p): p for p in (settings.vault_path / "Captures").glob("*.md")}
    for (key, idx), status in edits.items():
        path = notes.get(key)
        if path is None:
            logger.info(
                "digest: stale marker ^sift-%s-%d (note gone); ignored", key, idx
            )
            continue
        post = frontmatter.load(path)
        items = post.metadata.get("action_items") or []
        if idx >= len(items):
            logger.info("digest: stale marker ^sift-%s-%d; ignored", key, idx)
            continue
        if items[idx].get("status") != status:
            items[idx]["status"] = status
            path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")


def _line(c: Candidate, box: str) -> str:
    text = _escape_md((c.item.text.splitlines() or [""])[0])
    bits = [f"- [{box}] {text}"]
    if c.item.due is not None:
        bits.append(f"— due {c.item.due.isoformat()}")
        if c.item.due < date.today():
            bits.append("**OVERDUE**")
    bits.append(f"({round(c.item.confidence * 100)}%)")
    bits.append(f"[[{c.note.stem}]]")
    bits.append(f"^sift-{c.key}-{c.index}")
    return " ".join(bits)


def refresh(settings: Settings) -> str:
    """Apply user edits, then re-render Action Review.md. Returns the digest path."""
    digest_path = settings.vault_path / "Action Review.md"
    _apply(digest_path, settings)

    candidates = collect(settings)
    pending = [c for c in candidates if c.item.status == "pending"]
    confirmed = [c for c in candidates if c.item.status == "confirmed"]
    dated = sorted(
        (c for c in pending if c.item.due is not None),
        key=lambda c: (c.item.due, -c.item.confidence),
    )
    undated = sorted(
        (c for c in pending if c.item.due is None), key=lambda c: -c.item.confidence
    )

    parts = ["# Action Review", "", _HEADER, ""]
    if dated:
        parts += ["## Due", *(_line(c, " ") for c in dated), ""]
    if undated:
        parts += ["## Undated", *(_line(c, " ") for c in undated), ""]
    if confirmed:
        parts += ["## Confirmed", *(_line(c, "x") for c in confirmed), ""]
    if not (dated or undated or confirmed):
        parts.append("No action-item candidates yet.")

    settings.vault_path.mkdir(parents=True, exist_ok=True)
    digest_path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    return str(digest_path)
