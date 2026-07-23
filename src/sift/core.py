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

    NOTE (F0): the quarantined interpreter arrives with F0.5/F3; until then every note
    carries a default Interpretation() (classification=reference, no summary/tags/action
    items). Extractors that are not built yet are logged and the item still gets a
    caption-only note — never silently dropped (CLAUDE.md Conventions).
    """
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
    for item in adapters[source].pull(settings, path):
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
        # Interpreter is F0.5/F3 — default Interpretation() until then (see docstring).
        note = Note(item=item, extraction=extraction, interpretation=Interpretation())
        report.notes.append(writer.write(note, settings))
        report.written += 1

    try:
        digest.refresh(settings)
    except NotImplementedError:
        report.errors.append("action-review digest not built yet (F6); refresh skipped")
    return report
