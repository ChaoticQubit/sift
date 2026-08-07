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
  3. topics — a two-level hub hierarchy: Topics/<domain>.md (the ~10 fixed
     human-scale models.Domain shelves) links Topics/<domain>/<tag>.md subtopic
     hubs, which wikilink every note carrying the tag. Regenerated (wiped and
     rebuilt) each run; pure derived state.

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
from ..vault import writer
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
        writer._atomic_write(path, frontmatter.dumps(post) + "\n")
        linked += 1
    return linked


# A subtopic tag earns its own hub page only when this many notes share it;
# rarer tags stay in note frontmatter (still searchable/RAG-visible) and their
# notes are listed directly on the domain page. Human-scale filter, decided
# 2026-08-07: one flat page per tag produced 4809 hubs, 71% single-note noise.
_MIN_HUB_NOTES = 2


def topics(settings: Settings) -> int:
    """Regenerate the two-level Topics/ hierarchy from note frontmatter.

    Topics/<domain>.md            — one hub per fixed models.Domain a note uses:
                                    links its subtopic hubs + notes with no hub tag.
    Topics/<domain>/<tag>.md      — one hub per tag with >= _MIN_HUB_NOTES notes
                                    inside that domain.

    Pure derived state: the whole folder is wiped and rebuilt each run, so
    renamed/vanished tags never leave stale hubs behind. Returns the domain count.
    """
    import shutil

    by_domain: dict[str, dict[str, list[str]]] = {}  # domain -> tag -> stems
    domain_notes: dict[str, set[str]] = {}  # domain -> all stems
    for note in sorted((settings.vault_path / "Captures").glob("*.md")):
        try:
            post = frontmatter.load(note)
        except OSError:
            continue
        domain = str(post.metadata.get("domain") or "misc")
        domain_notes.setdefault(domain, set()).add(note.stem)
        for tag in post.metadata.get("tags") or []:
            slug = _TAG_SLUG.sub("-", str(tag).lower()).strip("-")
            if slug:
                by_domain.setdefault(domain, {}).setdefault(slug, []).append(note.stem)

    topics_dir = settings.vault_path / "Topics"
    if topics_dir.exists():
        shutil.rmtree(topics_dir)
    if not domain_notes:
        return 0
    topics_dir.mkdir(parents=True)

    for domain, stems in sorted(domain_notes.items()):
        tags = by_domain.get(domain, {})
        hubs = {t: s for t, s in tags.items() if len(set(s)) >= _MIN_HUB_NOTES}
        in_hubs: set[str] = set()
        if hubs:
            (topics_dir / domain).mkdir()
        for tag, tag_stems in sorted(hubs.items()):
            uniq = sorted(set(tag_stems))
            in_hubs.update(uniq)
            links = "\n".join(f"- [[{s}]]" for s in uniq)
            (topics_dir / domain / f"{tag}.md").write_text(
                f"---\ntype: topic\ndomain: {domain}\ntag: {tag}\n---\n\n{links}\n",
                encoding="utf-8",
            )
        parts = [f"---\ntype: domain\ndomain: {domain}\n---\n"]
        if hubs:
            parts.append("## Subtopics\n")
            parts.append("\n".join(f"- [[{t}]]" for t in sorted(hubs)) + "\n")
        loose = sorted(stems - in_hubs)
        if loose:
            parts.append("## Notes\n")
            parts.append("\n".join(f"- [[{s}]]" for s in loose) + "\n")
        (topics_dir / f"{domain}.md").write_text("\n".join(parts), encoding="utf-8")
    return len(domain_notes)
