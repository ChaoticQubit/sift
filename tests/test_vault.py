"""Frontmatter mapping + idempotent writer — offline, tmp vault only."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import frontmatter

from sift.db import storage
from sift.models import (
    ActionItem,
    Classification,
    Extraction,
    IngestVia,
    Interpretation,
    Item,
    Medium,
    Note,
)
from sift.vault import writer
from sift.vault.frontmatter import to_frontmatter

CAPTION = "Brown butter cookies 🍪 recipe below..."


def _note(**interp_kwargs) -> Note:
    item = Item(
        id="hash-abc123",
        permalink="https://www.instagram.com/reel/ABC123/",
        medium=Medium.reel,
        caption=CAPTION,
        ingest_via=IngestVia.dyi_export,
        saved_collection="Recipes",
        saved_at=date(2026, 3, 12),
        source_export="instagram-2026-07-20",
        media_files=["media/reel_ABC123.mp4"],
    )
    extraction = Extraction(text=item.caption, languages=["en"])
    return Note(
        item=item, extraction=extraction, interpretation=Interpretation(**interp_kwargs)
    )


def test_frontmatter_golden():
    note = _note(
        classification=Classification.educational,
        summary="A cookie recipe.",
        tags=["recipe", "dessert"],
        action_items=[
            ActionItem(
                text="Apply to the X fellowship", due=date(2026, 8, 1), confidence=0.7
            )
        ],
    )
    assert to_frontmatter(note) == {
        "type": "capture",
        "source": "https://www.instagram.com/reel/ABC123/",
        "platform": "instagram",
        "medium": "reel",
        "ingest_via": "dyi_export",
        "caption": CAPTION,
        "classification": "educational",
        "saved_collection": "Recipes",
        "saved_at": date(2026, 3, 12),
        "created": date.today(),
        "languages": ["en"],
        "has_action_items": True,
        "action_items": [
            {
                "text": "Apply to the X fellowship",
                "due": date(2026, 8, 1),
                "confidence": 0.7,
                "status": "pending",
            }
        ],
        "links": [],
        "media_files": ["media/reel_ABC123.mp4"],
        "source_export": "instagram-2026-07-20",
        "tags": ["recipe", "dessert"],
    }


def test_frontmatter_omits_absent_optionals():
    note = _note()
    note.item.permalink = None
    note.item.saved_collection = None
    note.item.saved_at = None
    note.item.source_export = None
    meta = to_frontmatter(note)
    assert not {"source", "saved_collection", "saved_at", "source_export"} & meta.keys()


def test_writer_idempotent_and_fenced(settings):
    note = _note()
    p1 = writer.write(note, settings)
    c1 = Path(p1).read_text()
    p2 = writer.write(note, settings)
    assert p1 == p2
    assert Path(p2).read_text() == c1
    assert [Path(p1)] == list((settings.vault_path / "Captures").glob("*.md"))
    post = frontmatter.loads(c1)
    assert post["caption"] == CAPTION  # verbatim in frontmatter
    assert (
        f"```\n{CAPTION}\n```" in post.content
    )  # fenced (render-defanged) in the body
    assert not storage.is_new(note.item.id, settings)  # writer marked it seen


def test_structured_body_renders_lists_and_source_link(settings):
    from sift.models import Resource

    note = _note(
        summary="A list of 3 system design books.",
        key_points=["DDIA covers storage internals", "SDI is interview prep"],
        resources=[
            Resource(name="Designing Data-Intensive Applications", kind="book"),
            Resource(name="System Design Interview", kind="book", detail="vol 1+2"),
        ],
        assessment="Solid starting list; pick DDIA first.",
    )
    body = frontmatter.loads(Path(writer.write(note, settings)).read_text()).content
    assert "## Key points" in body and "- DDIA covers storage internals" in body
    assert "## Resources" in body
    assert "- **Designing Data-Intensive Applications** (book)" in body
    assert "(book) - vol 1\\+2" in body
    assert "## Take" in body and "pick DDIA first" in body
    assert "[Original post](https://www.instagram.com/reel/ABC123/)" in body
    # Section order: substance first, verbatim caption after.
    assert body.index("## Key points") < body.index("## Caption")


def test_writer_refreshes_same_note_when_caption_changes(settings):
    note = _note()
    p1 = writer.write(note, settings)
    note.item.caption = "Edited caption entirely different"
    note.extraction.text = note.item.caption
    p2 = writer.write(note, settings)
    assert p1 == p2  # identity is Item.id, not the content-derived slug
    assert [Path(p1)] == list((settings.vault_path / "Captures").glob("*.md"))
    assert "Edited caption entirely different" in Path(p2).read_text()


def test_entertainment_routes_to_one_liner(settings):
    note = _note(
        classification=Classification.entertainment, summary="A funny cat video."
    )
    body = frontmatter.loads(Path(writer.write(note, settings)).read_text()).content
    assert len(body.splitlines()) == 1
    assert "cat video" in body


def test_one_liner_never_uses_caption(settings):
    note = _note(classification=Classification.entertainment)  # empty summary
    note.item.caption = "visit http://phish.example now"
    body = frontmatter.loads(Path(writer.write(note, settings)).read_text()).content
    assert "phish.example" not in body  # caption text must not appear unfenced
    assert note.item.permalink in body


def test_writer_reforces_status_pending(settings):
    note = _note(action_items=[ActionItem(text="buy tickets", confidence=0.9)])
    note.interpretation.action_items[0].status = "confirmed"  # a hijacked model's claim
    meta = frontmatter.loads(Path(writer.write(note, settings)).read_text()).metadata
    assert [ai["status"] for ai in meta["action_items"]] == ["pending"]
