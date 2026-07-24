"""Graph layer offline tests: dedupe-merge, Related wikilinks, topic hubs."""

from __future__ import annotations

from types import SimpleNamespace

import frontmatter

from sift import core
from sift.db import storage
from sift.models import (
    Extraction,
    IngestVia,
    Interpretation,
    Item,
    Medium,
    Note,
    RunReport,
)
from sift.rag import link
from sift.vault import writer


class FakeHandle:
    """Stands in for the agno Knowledge handle: canned hybrid-search results."""

    def __init__(self, docs=()):
        self.docs = list(docs)
        self.added = []

    def search(self, query, max_results=5):
        return self.docs[:max_results]

    def add_content(self, **kwargs):
        self.added.append(kwargs)


def _doc(path, content, name=None):
    return SimpleNamespace(
        content=content, meta_data={"path": str(path)}, name=name or "x"
    )


def _item(caption, shortcode="AAA"):
    return Item(
        id=f"id-{shortcode}",
        permalink=f"https://www.instagram.com/p/{shortcode}/",
        medium=Medium.image,
        caption=caption,
        ingest_via=IngestVia.dyi_export,
    )


LONG = (
    "ten ways to reduce agent token usage with prompt caching, context pruning, "
    "and semantic deduplication in production agentic frameworks"
)


def _write_note(settings, caption, shortcode, tags=()):
    note = Note(
        item=_item(caption, shortcode),
        extraction=Extraction(text=caption),
        interpretation=Interpretation(tags=list(tags)),
    )
    return writer.write(note, settings)


def test_find_duplicate_matches_same_content_only(settings):
    handle = FakeHandle([_doc("/v/existing.md", f"## Caption\n\n```\n{LONG}\n```")])
    assert link.find_duplicate(LONG, settings, handle=handle) == "/v/existing.md"
    other = (
        "completely different reel about sourdough starters and hydration ratios today"
    )
    assert link.find_duplicate(other, settings, handle=handle) is None
    # Trivial text is never judged — it would Jaccard-match everything.
    assert link.find_duplicate("so true", settings, handle=handle) is None


def test_process_folds_duplicate_into_existing_page_without_model_call(
    settings, fake_model, monkeypatch
):
    monkeypatch.setattr(
        "sift.extract.router.extract",
        lambda item, s: Extraction(text=item.caption),
    )
    first = _write_note(settings, LONG, "AAA")
    handle = FakeHandle([_doc(first, LONG)])
    report = RunReport()
    path = core.process(_item(LONG, "BBB"), settings, report, rag=handle)

    assert path == first and report.merged == 1 and report.written == 0
    assert fake_model.prompts == []  # duplicate burned NO model call
    post = frontmatter.load(first)
    assert post.metadata["sources"] == ["https://www.instagram.com/p/BBB/"]
    assert not storage.is_new("id-BBB", settings)  # rerun skips the dupe


def test_seen_text_catches_same_run_duplicates(settings, fake_model, monkeypatch):
    monkeypatch.setattr(
        "sift.extract.router.extract",
        lambda item, s: Extraction(text=item.caption),
    )
    seen = link.SeenText(settings.merge_similarity)
    report = RunReport()
    first = core.process(_item(LONG, "AAA"), settings, report, seen=seen)
    second = core.process(_item(LONG, "BBB"), settings, report, seen=seen)
    assert first == second and report.written == 1 and report.merged == 1
    assert len(fake_model.prompts) == 1  # the dupe burned no second model call


def test_enrich_adds_related_wikilinks_idempotently(settings, fake_model):
    path = _write_note(settings, LONG, "AAA")
    handle = FakeHandle(
        [_doc("/v/graphify-note-1234.md", "x"), _doc(path, "self — must be skipped")]
    )
    for _ in range(2):  # run twice: section replaced, not duplicated
        assert link.enrich([path], settings, handle=handle) == 1
    post = frontmatter.load(path)
    assert post.metadata["related"] == ["[[graphify-note-1234]]"]
    assert post.content.count("## Related") == 1
    assert "- [[graphify-note-1234]]" in post.content


def test_topics_builds_tag_hub_pages(settings, fake_model):
    _write_note(settings, LONG, "AAA", tags=["agentic-framework", "Token Reduction"])
    _write_note(
        settings,
        "another capture about agent frameworks",
        "BBB",
        tags=["agentic-framework"],
    )
    assert link.topics(settings) == 2
    hub = settings.vault_path / "Topics" / "agentic-framework.md"
    assert hub.read_text().count("- [[") == 2
    assert (settings.vault_path / "Topics" / "token-reduction.md").exists()


def test_merge_source_is_idempotent(settings):
    path = _write_note(settings, LONG, "AAA")
    dupe = _item(LONG, "BBB")
    for _ in range(2):
        writer.merge_source(path, dupe, settings)
    post = frontmatter.load(path)
    assert post.metadata["sources"] == ["https://www.instagram.com/p/BBB/"]
    assert post.content.count("duplicate save") == 0  # same caption -> no re-append
