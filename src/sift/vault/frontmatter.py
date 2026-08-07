"""Build / parse the capture-note frontmatter (Life-Planner-compatible). DESIGN.md §6."""

from __future__ import annotations

from datetime import date

from ..models import ActionItem, Note


def _action_item(ai: ActionItem) -> dict:
    d: dict = {"text": ai.text}
    if ai.due is not None:
        d["due"] = ai.due
    d["confidence"] = ai.confidence
    d["status"] = ai.status
    if ai.priority is not None:
        d["priority"] = ai.priority
    return d


def to_frontmatter(note: Note) -> dict:
    """Map a Note to the §6 frontmatter dict. Absent optionals are omitted, not null."""
    item, interp, extraction = note.item, note.interpretation, note.extraction
    meta: dict = {"type": "capture"}
    if item.permalink:
        meta["source"] = item.permalink
    meta["platform"] = "instagram"
    meta["medium"] = item.medium.value
    meta["ingest_via"] = item.ingest_via.value
    meta["caption"] = item.caption  # FIRST-CLASS, verbatim from the Item
    meta["classification"] = interp.classification.value
    meta["domain"] = interp.domain.value
    if item.saved_collection:
        meta["saved_collection"] = item.saved_collection
    if item.saved_at is not None:
        meta["saved_at"] = item.saved_at
    meta["created"] = date.today()
    meta["languages"] = list(extraction.languages)
    meta["has_action_items"] = bool(interp.action_items)
    meta["action_items"] = [_action_item(ai) for ai in interp.action_items]
    meta["links"] = list(extraction.artifacts.get("links", []))
    meta["media_files"] = list(item.media_files)
    if item.source_export:
        meta["source_export"] = item.source_export
    meta["tags"] = list(interp.tags)
    return meta
