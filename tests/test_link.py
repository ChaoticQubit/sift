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


def _write_note(settings, caption, shortcode, tags=(), domain="misc"):
    note = Note(
        item=_item(caption, shortcode),
        extraction=Extraction(text=caption),
        interpretation=Interpretation(tags=list(tags), domain=domain),
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


def test_reanalysis_of_own_note_is_a_refresh_not_a_merge(
    settings, fake_model, monkeypatch
):
    """Re-running an item whose note is already indexed must rewrite the note,
    never fold the item into itself as a 'duplicate'."""
    monkeypatch.setattr(
        "sift.extract.router.extract",
        lambda item, s: Extraction(text=item.caption),
    )
    first = _write_note(settings, LONG, "AAA")
    handle = FakeHandle([_doc(first, LONG)])  # the index serves its OWN note back
    report = RunReport()
    path = core.process(_item(LONG, "AAA"), settings, report, rag=handle)

    assert path == first and report.merged == 0 and report.written == 1
    assert len(fake_model.prompts) == 1  # refreshed through the full pipeline
    assert "sources" not in frontmatter.load(first).metadata  # no self-merge


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


def test_empty_content_skips_the_model_call(settings, fake_model, monkeypatch):
    monkeypatch.setattr(
        "sift.extract.router.extract", lambda item, s: Extraction(text="")
    )
    report = RunReport()
    core.process(_item("", "EMPTY"), settings, report)
    assert report.written == 1 and report.errors == []
    assert fake_model.prompts == []  # nothing to classify -> no model call


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


def test_topics_builds_domain_then_subtopic_hierarchy(settings, fake_model):
    _write_note(
        settings,
        LONG,
        "AAA",
        tags=["agentic-framework", "Token Reduction"],
        domain="technology",
    )
    _write_note(
        settings,
        "another capture about agent frameworks",
        "BBB",
        tags=["agentic-framework"],
        domain="technology",
    )
    _write_note(settings, "amazing pasta in rome", "CCC", tags=["italy"], domain="travel")
    assert link.topics(settings) == 2  # two domains in use

    topics = settings.vault_path / "Topics"
    # Shared tag (2 notes) -> a subtopic hub nested under its domain.
    hub = topics / "technology" / "agentic-framework.md"
    assert hub.read_text().count("- [[") == 2
    assert "[[agentic-framework]]" in (topics / "technology.md").read_text()
    # Singleton tags get NO hub page; their notes list directly on the domain page.
    assert not (topics / "technology" / "token-reduction.md").exists()
    assert not (topics / "travel" / "italy.md").exists()
    assert "- [[" in (topics / "travel.md").read_text()


def test_topics_wipes_stale_hubs_and_defaults_missing_domain_to_misc(
    settings, fake_model
):
    topics = settings.vault_path / "Topics"
    topics.mkdir(parents=True)
    (topics / "renamed-away.md").write_text("stale flat hub")
    _write_note(settings, LONG, "AAA", tags=["old-tag"])  # no domain in kwargs -> misc
    assert link.topics(settings) == 1
    assert not (topics / "renamed-away.md").exists()  # derived state fully rebuilt
    assert "- [[" in (topics / "misc.md").read_text()


def test_interpreter_off_enum_domain_degrades_to_misc_not_rejected():
    from sift.models import Domain

    assert Interpretation(domain="tech").domain is Domain.misc
    assert Interpretation(domain="TRAVEL ").domain is Domain.travel
    assert Interpretation().domain is Domain.misc


def test_merge_source_is_idempotent(settings):
    path = _write_note(settings, LONG, "AAA")
    dupe = _item(LONG, "BBB")
    for _ in range(2):
        writer.merge_source(path, dupe, settings)
    post = frontmatter.load(path)
    assert post.metadata["sources"] == ["https://www.instagram.com/p/BBB/"]
    assert post.content.count("duplicate save") == 0  # same caption -> no re-append
