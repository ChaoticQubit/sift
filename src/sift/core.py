"""The single public entrypoint. A future Life-Planner agent imports THIS (CLAUDE.md rule 6)."""
from __future__ import annotations

from .config import Settings
from .models import RunReport


def run(source: str, settings: Settings, path: str | None = None) -> RunReport:
    """Ingest via one adapter and process NEW items into the vault.

    source: "cookie_fetch" | "share_capture" | "dyi_export"  (DESIGN.md §4)
    path:   the export ZIP path for dyi_export; unused otherwise.

    Pipeline (DESIGN.md §5, AGENTS.md §2):
        items = ingest.<source>.pull(settings, path)              # normalized Items w/ captions
        for item in items:
            if not db.storage.is_new(item.id, settings): continue  # dedup ledger
            extraction = extract.router.extract(item, settings)    # deterministic: caption/OCR/STT/link
            interp = agents.interpreter.interpret(item, extraction, settings)
                                                                   # QUARANTINED, tool-less: sanitized
                                                                   # text -> validated Interpretation
            note = Note(item=item, extraction=extraction, interpretation=interp)
            report.notes.append(vault.writer.write(note, settings))  # deterministic; verbatim caption
        review.digest.refresh(settings)
        return report

    Hard rules: no instagrapi; account code read-only; Rule of Two structural (only the
    interpreter sees untrusted text, and it has no tools); idempotent.
    """
    raise NotImplementedError("F0/F1: implement adapter dispatch + pipeline wiring")
