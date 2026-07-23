"""Thin typer shell. NO business logic here (CLAUDE.md rule 6)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from .config import load_settings

app = typer.Typer(help="Sift — Instagram saves -> local second brain")


@app.command()
def run(source: str = "cookie_fetch", path: Optional[Path] = None) -> None:
    """Ingest + process. source: cookie_fetch | share_capture | dyi_export."""
    from . import core

    report = core.run(source, load_settings(), str(path) if path else None)
    typer.echo(report.model_dump_json(indent=2))


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


if __name__ == "__main__":
    app()
