"""The single public entrypoint. A future Life-Planner agent imports THIS (CLAUDE.md rule 6)."""

from __future__ import annotations

import hashlib
import logging

from .config import Settings
from .models import Extraction, Interpretation, Item, Note, RunReport

logger = logging.getLogger(__name__)


def _rag(settings: Settings):
    """One per-run Knowledge handle, or None when [rag] is absent/unreachable.
    Everything graph/index-shaped degrades gracefully without it."""
    try:
        from .rag import knowledge

        handle = knowledge._knowledge(settings)
        if err := knowledge.embed_probe(settings, handle=handle):
            logger.warning(
                "embedder down (%s); dedup and graph links degraded this run", err
            )
            return None
        return handle
    except Exception as exc:
        logger.debug("rag unavailable (%s); notes stay unindexed this run", exc)
        return None


def _seen(settings: Settings):
    """The per-run in-memory dedupe cache (rag/link.py SeenText), or None."""
    try:
        from .rag import link

        return link.SeenText(settings.merge_similarity)
    except Exception as exc:  # pragma: no cover — link has no heavy deps
        logger.debug("in-run dedupe unavailable (%s)", exc)
        return None


def process(
    item: Item, settings: Settings, report: RunReport, rag=None, seen=None
) -> str:
    """Extract -> dedupe -> interpret -> write ONE item (no ledger gate) — the shared
    per-item pipeline for run() and the media-backfill chunks (ingest/media_fetch).

    A near-duplicate (same content re-saved under another permalink) folds into its
    existing page and SKIPS the model call (rag/link.py): `seen` catches dupes
    within this run, the index catches dupes against earlier runs. Off-schema or
    failed interpreter output is logged, counted as a guardrail block, and the item
    falls back to a default Interpretation() — it still gets a note, never silently
    dropped (CLAUDE.md Conventions).
    """
    from .agents import interpreter
    from .extract import router
    from .vault import writer

    try:
        extraction = router.extract(item, settings)
    except NotImplementedError as exc:
        msg = f"{item.id}: {item.medium.value} extractor not ready ({exc}); caption-only note"
        logger.warning(msg)
        report.errors.append(msg)
        extraction = Extraction(text=item.caption)

    text = f"{item.caption}\n\n{extraction.text}"
    dup = seen.check(text) if seen is not None else None
    if dup is None and rag is not None:
        try:
            from .rag import link

            dup = link.find_duplicate(text, settings, handle=rag)
        except Exception as exc:
            logger.debug("duplicate check skipped (%s)", exc)
    if dup and f"-{hashlib.sha256(item.id.encode()).hexdigest()[:8]}.md" in dup:
        dup = None  # its OWN note from an earlier run — a refresh, not a duplicate
    if dup:
        path = writer.merge_source(dup, item, settings)
        report.merged += 1
        report.notes.append(path)  # content changed -> finalize re-indexes it
        logger.info("near-duplicate folded into %s: %s", path, item.id)
        return path

    if not text.strip():
        # Nothing to classify (e.g. a DM attachment missing from the export) —
        # don't burn a model call asking the interpreter to read an empty string.
        logger.info("%s: no content; default note without model call", item.id)
        interpretation = Interpretation()
    else:
        try:
            interpretation = interpreter.interpret(item, extraction, settings)
        except interpreter.InterpretationError as exc:
            msg = f"{item.id}: interpreter output rejected ({exc}); default note"
            logger.warning(msg)
            report.errors.append(msg)
            report.guardrail_blocks += 1
            interpretation = Interpretation()
    item = _dispose_media(item, interpretation, settings)
    note = Note(item=item, extraction=extraction, interpretation=interpretation)
    path = writer.write(note, settings)
    report.notes.append(path)
    report.written += 1
    if seen is not None:
        seen.add(text, path)
    return path


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def _dispose_media(item: Item, interpretation: Interpretation, settings: Settings):
    """Downloaded media is analysis input, not vault content: the note (summary,
    key points, transcript, OCR text, permalink) is the keeper. A staged image is
    copied into the vault ONLY when the interpreter judged it a dense visual
    reference worth reopening; videos never (their substance is the transcript).
    Vault-relative media (the captain's own DM attachments from an export) always
    stays — only absolute staging paths are disposed."""
    from pathlib import Path

    kept: list[str] = []
    changed = False
    for name in item.media_files:
        staged = Path(name)
        if not staged.is_absolute():
            kept.append(name)
            continue
        changed = True
        if interpretation.keep_media and staged.suffix.lower() in _IMAGE_SUFFIXES:
            media_dir = settings.vault_path / "media"
            media_dir.mkdir(parents=True, exist_ok=True)
            dest = media_dir / staged.name
            dest.write_bytes(staged.read_bytes())
            kept.append(f"media/{dest.name}")
            logger.info("kept reference image: %s", dest.name)
    return item.model_copy(update={"media_files": kept}) if changed else item


def _defer_to_media_queue(item: Item, settings: Settings) -> bool:
    """True when this item should wait for the chunked media backfill instead of
    being interpreted caption-only: an Instagram POST permalink with no media on
    disk. Post-shaped URLs only — a shared PROFILE link must never reach the
    fetch queue (gallery-dl would pull the whole feed); it gets a normal caption
    note instead. One model call per item — the chunk run analyzes it WITH its
    media (OCR/STT)."""
    from .guardrails.egress import is_instagram_post_url

    if not settings.media_defer or item.media_files or not item.permalink:
        return False
    return is_instagram_post_url(item.permalink)


def run(
    source: str,
    settings: Settings,
    path: str | None = None,
    defer_media: bool | None = None,
) -> RunReport:
    """Ingest via one adapter and process NEW items into the vault.

    source: "cookie_fetch" | "share_capture" | "dyi_export"  (DESIGN.md §4)
    path:   the export ZIP path for dyi_export; unused otherwise.
    defer_media: overrides settings.media_defer for this run (None = use the setting).

    Pipeline (DESIGN.md §5, AGENTS.md §2): ingest -> dedup ledger -> deterministic
    extract -> interpret -> deterministic write -> graph linking + digest refresh.
    Hard rules: no instagrapi; account code read-only; Rule of Two structural (only
    the interpreter sees untrusted text, and it has no tools); idempotent.

    With media_defer on, Instagram-permalink items lacking media bytes are queued in
    the SQLite media_queue instead of being interpreted caption-only; the chunked
    backfill (`sift fetch-media`) fetches their media and runs the one full analysis.
    """
    from .db import storage
    from .ingest import cookie_fetch, dyi_export, share_capture

    adapters = {
        "cookie_fetch": cookie_fetch,
        "share_capture": share_capture,
        "dyi_export": dyi_export,
    }
    if source not in adapters:
        raise ValueError(
            f"unknown source {source!r}; expected one of {sorted(adapters)}"
        )
    if defer_media is not None:
        settings = settings.model_copy(update={"media_defer": defer_media})

    report = RunReport()
    rag = _rag(settings)
    seen = _seen(settings)
    # Adapters count their own skips/missing-media into the report (log-and-count,
    # never silent drops — CLAUDE.md Conventions).
    for item in adapters[source].pull(settings, path, report):
        report.processed += 1
        if not storage.is_new(item.id, settings):
            report.skipped += 1
            logger.info("skipped (already in ledger): %s", item.id)
            continue
        if _defer_to_media_queue(item, settings):
            storage.enqueue_media(item, settings)
            storage.mark_seen(item.id, settings)  # the queue owns it now; reruns skip
            report.deferred += 1
            logger.info("deferred to media queue: %s", item.id)
            continue
        process(item, settings, report, rag=rag, seen=seen)

    finalize(report, settings, rag=rag)
    return report


def finalize(report: RunReport, settings: Settings, rag=None) -> None:
    """Index + graph-link this run's notes and refresh the digest — derived state,
    never fatal: any failure is logged and counted; `sift index` / `sift review`
    repair later."""
    from .review import digest

    if report.notes:
        try:
            from .rag import knowledge, link

            handle = rag or knowledge._knowledge(settings)
            if err := knowledge.embed_probe(settings, handle=handle):
                raise RuntimeError(f"embedding backend down: {err}")
            for note_path in report.notes:
                knowledge.index_note(note_path, settings, knowledge=handle)
            link.enrich(report.notes, settings, handle=handle)
            link.topics(settings)
        except ImportError:
            msg = "search index skipped — install with: pip install 'sift[rag]'"
            logger.warning(msg)
            report.errors.append(msg)
        except Exception as exc:
            msg = (
                f"indexing/graph linking failed ({exc}); notes written, "
                "run `sift index` later"
            )
            logger.warning(msg)
            report.errors.append(msg)

    # The digest is derived state like the index: failure is logged and counted,
    # never fatal — `sift review` regenerates it.
    try:
        digest.refresh(settings)
    except Exception as exc:
        msg = f"action-review digest refresh failed ({exc}); notes written"
        logger.warning(msg)
        report.errors.append(msg)
