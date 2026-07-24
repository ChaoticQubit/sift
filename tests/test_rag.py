"""F5 RAG indexing + deterministic search — offline, the Knowledge handle mocked.

The real LanceDB/embedder path needs the [rag] extra and an embedding endpoint;
tests mock `_knowledge` (CLAUDE.md Conventions) and assert the guardrail contract:
embed-bound text is PII-masked, the vault note stays verbatim, search is a plain
query with no agent involved.
"""

from __future__ import annotations

from types import SimpleNamespace

import frontmatter
import pytest

from sift import core
from sift.models import IngestVia, Item, Medium
from sift.rag import knowledge, search


@pytest.fixture
def fake_knowledge(monkeypatch):
    """Records add_content calls; returns canned search results."""
    handle = SimpleNamespace(added=[], results=[])
    handle.add_content = lambda **kw: handle.added.append(kw)
    handle.search = lambda query, max_results: handle.results
    monkeypatch.setattr("sift.rag.knowledge._knowledge", lambda s: handle)
    return handle


def _write_note(settings, name: str, caption: str, body: str, permalink: str) -> str:
    captures = settings.vault_path / "Captures"
    captures.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(
        body, caption=caption, source=permalink, classification="reference"
    )
    path = captures / f"{name}.md"
    path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")
    return str(path)


class TestIndexing:
    def test_embed_bound_text_is_masked_vault_stays_verbatim(
        self, settings, fake_knowledge
    ):
        caption = "Call me: my SSN is 078-05-1120 and email is a@b.com"
        path = _write_note(
            settings, "pii-note", caption, "body text", "https://x.example/p/1"
        )

        knowledge.index_note(path, settings)

        [call] = fake_knowledge.added
        assert "078-05-1120" not in call["text_content"]  # masked on the embed path
        assert "a@b.com" not in call["text_content"]
        assert "body text" in call["text_content"]
        assert call["upsert"] is True
        assert call["metadata"]["permalink"] == "https://x.example/p/1"
        # The vault note itself is untouched — verbatim caption (AGENTS.md §3a).
        assert (
            "078-05-1120"
            in (settings.vault_path / "Captures" / "pii-note.md").read_text()
        )

    def test_build_sweeps_all_captures(self, settings, fake_knowledge):
        _write_note(settings, "one", "first", "b1", "https://x.example/p/1")
        _write_note(settings, "two", "second", "b2", "https://x.example/p/2")

        assert knowledge.build(settings) == 2
        assert {c["name"] for c in fake_knowledge.added} == {"one", "two"}


class TestSearch:
    def test_query_formats_ranked_hits(self, settings, fake_knowledge):
        fake_knowledge.results = [
            SimpleNamespace(
                content="Feed the sourdough starter twice a day " * 20,
                meta_data={
                    "path": "/v/Captures/a.md",
                    "permalink": "https://x.example/p/1",
                },
                name="a",
            )
        ]
        [line] = search.query("sourdough", settings)
        assert line.startswith("/v/Captures/a.md — https://x.example/p/1")
        assert "sourdough starter" in line
        assert len(line) < 300  # snippet capped


class TestCoreWiring:
    def _run(self, settings, monkeypatch):
        items = [
            Item(
                id="rag-1",
                medium=Medium.text,
                caption="Brown butter cookies",
                ingest_via=IngestVia.dyi_export,
            )
        ]
        monkeypatch.setattr(
            "sift.ingest.dyi_export.pull", lambda s, p=None, report=None: items
        )
        return core.run("dyi_export", settings, path="ignored.zip")

    def test_written_notes_are_indexed(
        self, settings, monkeypatch, fake_model, fake_knowledge
    ):
        report = self._run(settings, monkeypatch)
        assert report.written == 1
        assert len(fake_knowledge.added) == 1
        assert not any("indexing" in e for e in report.errors)

    def test_missing_rag_extra_degrades_with_hint(
        self, settings, monkeypatch, fake_model
    ):
        def no_extra(s):
            raise ImportError("No module named 'lancedb'")

        monkeypatch.setattr("sift.rag.knowledge._knowledge", no_extra)
        report = self._run(settings, monkeypatch)
        assert report.written == 1  # notes still written
        assert any("sift[rag]" in e for e in report.errors)

    def test_embed_failure_degrades_run_still_succeeds(
        self, settings, monkeypatch, fake_model
    ):
        def down(s):
            raise ConnectionError("embed server down")

        monkeypatch.setattr("sift.rag.knowledge._knowledge", down)
        report = self._run(settings, monkeypatch)
        assert report.written == 1
        assert any("sift index" in e for e in report.errors)
