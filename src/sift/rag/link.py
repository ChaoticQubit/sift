"""Graph layer: connect notes into a second brain — deterministic, no agent. DESIGN.md §10.

Layered on the SAME hybrid LanceDB index `sift search` uses (rag/knowledge.py),
three mechanisms turn isolated capture notes into a connected Obsidian graph:

  1. find_duplicate — BEFORE interpretation, core.process asks whether this item's
     content already lives in the vault. Hybrid search picks candidates; a token
     Jaccard ratio on the indexed copy decides (agno's hybrid API exposes no
     similarity score). A true dupe folds into the existing page as an extra
     source (vault/writer.merge_source) and never burns a second model call.
  2. enrich — after a run, every new note gets `related:` frontmatter and a
     "## Related" section of wikilinks to its nearest neighbors: the graph edges.
  3. topics — Topics/<tag>.md hub pages wikilink every note carrying the tag, so
     categories cluster the graph. Regenerated each run; pure derived state.

The only model-bound data is the PII-MASKED query text sent to the embedder —
the same masking contract as indexing (AGENTS.md §3a). Failures here are the
caller's log-and-continue problem, never fatal to a run.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import frontmatter

from ..config import Settings
from ..guardrails.pii import mask_for_model
from . import knowledge as kn

logger = logging.getLogger(__name__)

_RELATED_MARK = "## Related"
_RELATED_SPLIT = re.compile(rf"\n+{_RELATED_MARK}\b.*", re.S)
# Trivial captions ("so true") would Jaccard-match everything; refuse to judge them.
_MIN_TOKENS = 8
_TAG_SLUG = re.compile(r"[^a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _similarity(a: str, b: str) -> float:
    """Token Jaccard — robust to the fencing/header noise in indexed note bodies."""
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


class SeenText:
    """In-memory dedupe cache for ONE run: catches duplicates saved within the same
    run, which the index can't (notes are indexed at finalize). Pure token compare,
    no embeds. ponytail: O(n^2) linear scan — fine to a few thousand items; switch
    to MinHash if a run ever gets huge."""

    def __init__(self, threshold: float):
        self.threshold = threshold
        self.rows: list[tuple[str, set[str]]] = []

    def check(self, text: str) -> str | None:
        toks = _tokens(text)
        if len(toks) < _MIN_TOKENS:
            return None
        for path, other in self.rows:
            union = toks | other
            if union and len(toks & other) / len(union) >= self.threshold:
                return path
        return None

    def add(self, text: str, path: str) -> None:
        self.rows.append((path, _tokens(text)))


def find_duplicate(text: str, settings: Settings, handle=None) -> str | None:
    """Path of an existing near-identical note, else None.

    text is this item's caption + extraction — masked before it reaches the
    embedder inside search(). Same content re-saved from a different permalink
    (reposted reel) scores high; two distinct posts on the same TOPIC do not —
    those get Related links instead of a merge.
    """
    masked = mask_for_model(text.strip())
    if len(_tokens(masked)) < _MIN_TOKENS:
        return None
    handle = handle or kn._knowledge(settings)
    for doc in handle.search(query=masked, max_results=3):
        meta = getattr(doc, "meta_data", None) or {}
        indexed = str(getattr(doc, "content", ""))
        path = meta.get("path", "")
        if not path or not indexed:
            continue
        score = _similarity(masked, indexed)
        if score >= settings.merge_similarity:
            logger.info("near-duplicate of %s (jaccard %.2f)", path, score)
            return path
    return None


def enrich(note_paths: list[str], settings: Settings, handle=None) -> int:
    """Wikilink each note to its nearest neighbors. Idempotent (section replaced).

    ponytail: top-K neighbors with no similarity floor — agno's hybrid search
    returns no score; add a floor by querying LanceDB directly if weak edges
    ever bother the graph."""
    handle = handle or kn._knowledge(settings)
    linked = 0
    for raw in note_paths:
        path = Path(raw)
        try:
            post = frontmatter.load(path)
        except OSError:
            continue
        query = kn._embed_text(post)
        if not query:
            continue
        stems: list[str] = []
        for doc in handle.search(query=query, max_results=settings.link_max + 3):
            meta = getattr(doc, "meta_data", None) or {}
            other = meta.get("path", "")
            stem = Path(other).stem if other else str(getattr(doc, "name", ""))
            if stem and stem != path.stem and stem not in stems:
                stems.append(stem)
            if len(stems) >= settings.link_max:
                break
        if not stems:
            continue
        post.metadata["related"] = [f"[[{s}]]" for s in stems]
        body = _RELATED_SPLIT.split(post.content)[0]
        links = "\n".join(f"- [[{s}]]" for s in stems)
        post.content = f"{body}\n\n{_RELATED_MARK}\n\n{links}"
        path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")
        linked += 1
    return linked


def topics(settings: Settings) -> int:
    """Regenerate Topics/<tag>.md hub pages from note tags. Pure derived state."""
    by_tag: dict[str, list[str]] = {}
    for note in sorted((settings.vault_path / "Captures").glob("*.md")):
        try:
            post = frontmatter.load(note)
        except OSError:
            continue
        for tag in post.metadata.get("tags") or []:
            slug = _TAG_SLUG.sub("-", str(tag).lower()).strip("-")
            if slug:
                by_tag.setdefault(slug, []).append(note.stem)
    if by_tag:
        topics_dir = settings.vault_path / "Topics"
        topics_dir.mkdir(parents=True, exist_ok=True)
        for slug, stems in by_tag.items():
            links = "\n".join(f"- [[{s}]]" for s in sorted(set(stems)))
            (topics_dir / f"{slug}.md").write_text(
                f"---\ntype: topic\ntag: {slug}\n---\n\n{links}\n", encoding="utf-8"
            )
    return len(by_tag)
