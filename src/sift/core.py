"""The single public entrypoint. A future Life-Planner agent imports THIS (CLAUDE.md rule 6)."""
from __future__ import annotations

from .config import Settings
from .models import RunReport


def run(source: str, settings: Settings, path: str | None = None) -> RunReport:
    """Ingest via one adapter and process NEW items into the vault.

    source: "cookie_fetch" | "share_capture" | "dyi_export"  (DESIGN.md §4)
    path:   the export ZIP path for dyi_export; unused otherwise.

    Pipeline (DESIGN.md §5, AGENTS.md):
        items = ingest.<source>.pull(settings, path)          # normalized Items w/ captions
        for item in dedup.new_only(items, settings):
            record = agents.reader.read(item, settings)       # QUARANTINED: caption/OCR/STT/link -> typed record
            note   = agents.enricher.enrich(record, settings) # PRIVILEGED: classify/summarize on SANITIZED data
            report.notes.append(vault.writer.write(note, settings))
        review.digest.refresh(settings)
        return report

    Hard rules: no instagrapi; account code read-only; Rule-of-Two enforced; idempotent.
    """
    raise NotImplementedError("F0/F1: implement adapter dispatch + Rule-of-Two wiring")
