"""core.run() with a monkeypatched fake ingest adapter — fully offline."""

from __future__ import annotations

import json
from pathlib import Path

import frontmatter
import pytest

from sift import core
from sift.models import IngestVia, Item, Medium

FIXTURES = Path(__file__).parent / "fixtures"


def _fake_items() -> list[Item]:
    return [
        Item(
            id="fake-1",
            permalink="https://www.instagram.com/reel/FAKEREEL01/",
            medium=Medium.text,
            caption="Brown butter cookies — recipe.",
            ingest_via=IngestVia.dyi_export,
        ),
        Item(
            id="fake-2",
            permalink="https://www.instagram.com/p/FAKEPOST02/",
            medium=Medium.image,
            caption="Apply by Aug 1.",
            ingest_via=IngestVia.dyi_export,
        ),
    ]


def test_unknown_source_raises(settings):
    with pytest.raises(ValueError, match="unknown source"):
        core.run("instagrapi", settings)


def test_run_counts_and_note_content(settings, monkeypatch, fake_model):
    monkeypatch.setattr("sift.ingest.dyi_export.pull", lambda s, p=None: _fake_items())

    report = core.run("dyi_export", settings, path="ignored.zip")
    assert (report.processed, report.written, report.skipped) == (2, 2, 0)
    # image extractor is F3: logged, item still written caption-only — never dropped.
    assert any("fake-2" in e for e in report.errors)
    # digest is F6: noted in the report, run does not crash.
    assert any("digest" in e for e in report.errors)

    posts = [frontmatter.loads(Path(p).read_text()) for p in report.notes]
    for post in posts:
        assert post["caption"]  # caption populated on every note
        assert f"```\n{post['caption']}\n```" in post.content  # and fenced in the body

    # Second run: the ledger skips everything, nothing is rewritten.
    report2 = core.run("dyi_export", settings)
    assert (report2.processed, report2.written, report2.skipped) == (2, 0, 2)


def test_fixtures_are_valid_json():
    saved = json.loads((FIXTURES / "saved_posts.json").read_text())
    assert len(saved["saved_saved_media"]) == 2
    thread = json.loads(
        (FIXTURES / "messages/inbox/self_notes/message_1.json").read_text()
    )
    assert thread["messages"]
