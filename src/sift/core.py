"""The single public entrypoint. A future Life-Planner agent imports THIS (CLAUDE.md rule 6)."""

from __future__ import annotations

import logging

from .config import Settings
from .models import Extraction, Interpretation, Note, RunReport

logger = logging.getLogger(__name__)


def run(source: str, settings: Settings, path: str | None = None) -> RunReport:
    """Ingest via one adapter and process NEW items into the vault.

    source: "cookie_fetch" | "share_capture" | "dyi_export"  (DESIGN.md §4)
    path:   the export ZIP path for dyi_export; unused otherwise.

    Pipeline (DESIGN.md §5, AGENTS.md §2): ingest -> dedup ledger -> deterministic
    extract -> interpret -> deterministic write -> digest refresh. Hard rules: no
    instagrapi; account code read-only; Rule of Two structural (only the interpreter
    sees untrusted text, and it has no tools); idempotent.

    Off-schema or failed interpreter output is logged, counted as a guardrail block,
    and the item falls back to a default Interpretation() — it still gets a note,
    never silently dropped (CLAUDE.md Conventions).
    """
    from .agents import interpreter
    from .db import storage
    from .extract import router
    from .ingest import cookie_fetch, dyi_export, share_capture
    from .review import digest
    from .vault import writer

    adapters = {
        "cookie_fetch": cookie_fetch,
        "share_capture": share_capture,
        "dyi_export": dyi_export,
    }
    if source not in adapters:
        raise ValueError(
            f"unknown source {source!r}; expected one of {sorted(adapters)}"
        )

    report = RunReport()
    # Adapters count their own skips/missing-media into the report (log-and-count,
    # never silent drops — CLAUDE.md Conventions).
    for item in adapters[source].pull(settings, path, report):
        report.processed += 1
        if not storage.is_new(item.id, settings):
            report.skipped += 1
            logger.info("skipped (already in ledger): %s", item.id)
            continue
        try:
            extraction = router.extract(item, settings)
        except NotImplementedError as exc:
            msg = f"{item.id}: {item.medium.value} extractor not ready ({exc}); caption-only note"
            logger.warning(msg)
            report.errors.append(msg)
            extraction = Extraction(text=item.caption)
        try:
            interpretation = interpreter.interpret(item, extraction, settings)
        except interpreter.InterpretationError as exc:
            msg = f"{item.id}: interpreter output rejected ({exc}); default note"
            logger.warning(msg)
            report.errors.append(msg)
            report.guardrail_blocks += 1
            interpretation = Interpretation()
        note = Note(item=item, extraction=extraction, interpretation=interpretation)
        report.notes.append(writer.write(note, settings))
        report.written += 1

    # Incremental RAG index of this run's notes (F5). The index is derived state:
    # any failure here is logged and counted, never fatal — `sift index` repairs.
    if report.notes:
        try:
            from .rag import knowledge

            handle = knowledge._knowledge(settings)
            for note_path in report.notes:
                knowledge.index_note(note_path, settings, knowledge=handle)
        except ImportError:
            msg = "search index skipped — install with: pip install 'sift[rag]'"
            logger.warning(msg)
            report.errors.append(msg)
        except Exception as exc:
            msg = (
                f"search indexing failed ({exc}); notes written, run `sift index` later"
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
    return report
