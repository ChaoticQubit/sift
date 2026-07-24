"""Thin typer shell. NO business logic here (CLAUDE.md rule 6)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from .config import load_settings

app = typer.Typer(help="Sift — Instagram saves -> local second brain")


@app.command()
def run(
    source: str = "cookie_fetch",
    path: Optional[Path] = None,
    defer_media: Optional[bool] = typer.Option(
        None,
        "--defer-media/--no-defer-media",
        help="Queue Instagram items without media bytes for chunked fetching "
        "instead of caption-only analysis (default: SIFT_MEDIA_DEFER).",
    ),
) -> None:
    """Ingest + process. source: cookie_fetch | share_capture | dyi_export."""
    from . import core

    report = core.run(
        source, load_settings(), str(path) if path else None, defer_media=defer_media
    )
    typer.echo(report.model_dump_json(indent=2))


@app.command()
def fetch_media() -> None:
    """Fetch real media for one even chunk of queued items, then fully analyze them.

    Schedulable daily until the queue drains; progress is auditable in the
    media_queue table of <work_dir>/ledger.db.
    """
    import json as _json

    from .db import storage
    from .ingest import media_fetch

    settings = load_settings()
    report = media_fetch.run(settings)
    typer.echo(report.model_dump_json(indent=2))
    typer.echo(_json.dumps({"media_queue": storage.media_counts(settings)}))


@app.command()
def search(query: str) -> None:
    """Search your processed captures."""
    from .rag import search as _search

    for hit in _search.query(query, load_settings()):
        typer.echo(hit)


@app.command()
def index(rebuild: bool = False) -> None:
    """Build / refresh the RAG index."""
    from .rag import knowledge

    knowledge.build(load_settings(), rebuild=rebuild)


@app.command()
def review() -> None:
    """Refresh Action Review.md (applies your confirm/dismiss checkboxes first)."""
    from .review import digest

    typer.echo(digest.refresh(load_settings()))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8595) -> None:
    """Run the share-capture endpoint (see docs/share-capture.md for phone setup)."""
    from .ingest import share_capture

    share_capture.serve(load_settings(), host=host, port=port)


if __name__ == "__main__":
    app()
