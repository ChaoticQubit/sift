"""Adapter: official 'Download Your Information' export ZIP. Zero ban risk. DESIGN.md §4.

Backfill + the ONLY safe route to already-sent self-DMs. Extracts captions too.

Parses (JSON export shape only — see pull() for the HTML refusal):
  - your_instagram_activity/saved/saved_posts.json (permalinks + timestamps; the
    saved list genuinely carries no captions, so those items get caption="")
  - your_instagram_activity/saved/saved_collections.json (names -> saved_collection)
  - your_instagram_activity/messages/inbox/*/message_*.json — the note-to-self
    thread is the one whose participants all share ONE name (real exports show
    both a single entry and the same name duplicated); shares carry the caption
    in share_text, plain text messages become Medium.text notes.

Mojibake: Meta double-encodes non-ASCII (UTF-8 bytes escaped as latin-1 code
points, e.g. "\\u00e2\\u0080\\u0099" for a right quote). Every string is repaired
via latin-1 re-encode -> UTF-8 decode, falling back to the original on failure.

Reads ZIP members via zipfile without extracting the archive; only message media
attachments are copied out, into <vault>/media/. Nothing is silently dropped:
unparseable entries and missing media are logged and counted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import zipfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath

from ..config import Settings
from ..models import IngestVia, Item, Medium, RunReport

logger = logging.getLogger(__name__)

_SAVED = "your_instagram_activity/saved/saved_posts.json"
_SAVED_HTML = "your_instagram_activity/saved/saved_posts.html"
_COLLECTIONS = "your_instagram_activity/saved/saved_collections.json"
_INBOX = "your_instagram_activity/messages/inbox"

# zipfile.Path (for a .zip) and pathlib.Path (for an extracted dir) share the
# subset used here: /, exists, is_dir, iterdir, name, read_bytes.
_Root = Path | zipfile.Path


def _fix_mojibake(s: str) -> str:
    """Reverse Meta's double encoding; leave already-correct text alone."""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _fix_tree(obj: object) -> object:
    """Apply the mojibake repair to every string in a parsed JSON tree."""
    if isinstance(obj, str):
        return _fix_mojibake(obj)
    if isinstance(obj, list):
        return [_fix_tree(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _fix_tree(v) for k, v in obj.items()}
    return obj


def _load_json(node: _Root) -> dict:
    return _fix_tree(json.loads(node.read_bytes()))  # type: ignore[return-value]


def _utc_date(epoch_s: float) -> date:
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).date()


def _item_id(key: str, when: str) -> str:
    """Stable id: sha256 of permalink + ISO date (or text/uri + timestamp)."""
    return hashlib.sha256(f"{key}{when}".encode()).hexdigest()


def _infer_medium(link: str) -> Medium:
    if "instagram.com" not in link:
        return Medium.link
    if "/reel/" in link or "/reels/" in link:
        return Medium.reel
    if "/tv/" in link:
        return Medium.video
    # /p/ and anything else: a carousel is indistinguishable from a single image
    # in the export, so it ingests as `image` (documented choice; refined at F2).
    return Medium.image


def _saved_items(
    root: _Root, source_export: str, counts: Counter, report: RunReport
) -> list[Item]:
    node = root / _SAVED
    if not node.exists():
        if (root / _SAVED_HTML).exists():
            raise ValueError(
                "this export is in HTML format, which Sift does not parse; "
                "re-request your 'Download Your Information' export with "
                "format = JSON and run again"
            )
        logger.warning("dyi_export: no %s in export; no saved posts to ingest", _SAVED)
        return []
    collections = _collection_map(root)
    items: list[Item] = []
    for entry in _load_json(node).get("saved_saved_media", []):
        href = entry.get("string_map_data", {}).get("Saved on", {}).get("href")
        if not href:
            counts["skipped_entries"] += 1
            msg = f"saved post {entry.get('title')!r} has no permalink; entry skipped"
            logger.warning("dyi_export: %s", msg)
            report.errors.append(f"dyi_export: {msg}")
            continue
        ts = entry["string_map_data"]["Saved on"].get("timestamp")
        saved_at = _utc_date(ts) if ts else None
        items.append(
            Item(
                id=_item_id(href, saved_at.isoformat() if saved_at else ""),
                permalink=href,
                medium=_infer_medium(href),
                caption="",  # the saved list genuinely carries no caption
                ingest_via=IngestVia.dyi_export,
                saved_collection=collections.get(href),
                saved_at=saved_at,
                source_export=source_export,
                raw={"title": entry.get("title", "")},
            )
        )
    return items


def _collection_map(root: _Root) -> dict[str, str]:
    """permalink -> collection name. The export is a flat list: a row whose Name
    has no href names a collection; following rows with hrefs are its members.
    Absent file = no collections; tolerated entirely."""
    node = root / _COLLECTIONS
    if not node.exists():
        return {}
    mapping: dict[str, str] = {}
    current: str | None = None
    for entry in _load_json(node).get("saved_saved_collections", []):
        name = entry.get("string_map_data", {}).get("Name", {})
        href = name.get("href")
        if href and current:
            mapping[href] = current
        elif not href:
            current = entry.get("title") or name.get("value")
    return mapping


def _self_thread_files(root: _Root, report: RunReport) -> list[_Root]:
    """message_*.json files of the note-to-self thread: every participant entry
    shares one name. Real exports have shown BOTH a single participant entry and
    the same name listed twice, so match on distinct names, not entry count.
    Self-DMs are a headline deliverable: finding none is always logged-and-counted,
    never a silent empty result."""
    inbox = root / _INBOX
    if not inbox.exists():
        msg = f"no {_INBOX} in export; no self-DMs to ingest"
        logger.warning("dyi_export: %s", msg)
        report.errors.append(f"dyi_export: {msg}")
        return []
    out: list[_Root] = []
    for thread in sorted(inbox.iterdir(), key=lambda p: p.name):
        if not thread.is_dir():
            continue
        files = sorted(
            (
                f
                for f in thread.iterdir()
                if f.name.startswith("message_") and f.name.endswith(".json")
            ),
            key=lambda p: p.name,
        )
        if files:
            names = {
                p.get("name") for p in _load_json(files[0]).get("participants", [])
            }
            if len(names) == 1:
                out.extend(files)
    if not out:
        msg = "inbox present but no note-to-self thread matched; no self-DMs ingested"
        logger.warning("dyi_export: %s", msg)
        report.errors.append(f"dyi_export: {msg}")
    return out


def _uri_safe(root: _Root, uri: str) -> bool:
    """Trust boundary (exempt from simplification — CLAUDE.md gate 5): the uri comes
    from untrusted export JSON. Reject absolute paths and `..` segments; in
    extracted-directory mode also require the resolved path (symlinks included)
    to stay inside the export root."""
    pure = PurePosixPath(uri)
    if pure.is_absolute() or ".." in pure.parts or not pure.name:
        return False
    if isinstance(root, Path):
        return (root / uri).resolve().is_relative_to(root.resolve())
    return True  # zipfile.Path: membership is name-matched against the archive list


def _copy_media(
    root: _Root, uris: list[str], settings: Settings, counts: Counter, report: RunReport
) -> list[str]:
    """Copy attachments that exist in the export into <vault>/media/; every
    rejected, missing, or non-file uri is logged and counted, never fatal."""
    copied: list[str] = []
    for uri in uris:
        name = re.sub(r"[^\w. -]", "_", PurePosixPath(uri).name).lstrip(".")
        if not _uri_safe(root, uri) or not name or len(name) > 255:
            counts["media_missing"] += 1
            msg = f"media uri {uri!r} refused (escapes export root or bad name); permalink-only"
            logger.warning("dyi_export: %s", msg)
            report.errors.append(f"dyi_export: {msg}")
            continue
        member = root / uri
        if not member.exists():
            counts["media_missing"] += 1
            logger.warning("dyi_export: media %s not in export; permalink-only", uri)
            continue
        if not member.is_file():
            counts["media_missing"] += 1
            msg = f"media uri {uri!r} is not a regular file; skipped"
            logger.warning("dyi_export: %s", msg)
            report.errors.append(f"dyi_export: {msg}")
            continue
        media_dir = settings.vault_path / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        # ponytail: basename collision across threads overwrites; hash-prefix if it bites
        dest = media_dir / name
        dest.write_bytes(member.read_bytes())
        copied.append(f"media/{dest.name}")
    return copied


def _message_item(
    msg: dict,
    root: _Root,
    settings: Settings,
    source_export: str,
    counts: Counter,
    report: RunReport,
) -> Item | None:
    ts_ms = msg.get("timestamp_ms")
    when = _utc_date(ts_ms / 1000) if ts_ms else None
    common = dict(
        ingest_via=IngestVia.dyi_export, saved_at=when, source_export=source_export
    )
    share = msg.get("share") or {}
    if share.get("link"):
        link = share["link"]
        return Item(
            id=_item_id(link, when.isoformat() if when else ""),
            permalink=link,
            medium=_infer_medium(link),
            caption=share.get("share_text") or "",  # share_text IS the caption
            raw={"original_content_owner": share.get("original_content_owner", "")},
            **common,
        )
    uris = [
        p["uri"] for p in msg.get("photos", []) + msg.get("videos", []) if p.get("uri")
    ]
    if uris:
        return Item(
            id=_item_id(uris[0], str(ts_ms)),
            medium=Medium.video if msg.get("videos") else Medium.image,
            caption=msg.get("content") or "",
            media_files=_copy_media(root, uris, settings, counts, report),
            **common,
        )
    if msg.get("content"):
        return Item(
            id=_item_id(msg["content"], str(ts_ms)),
            medium=Medium.text,
            caption=msg["content"],  # a plain text note: the text is the caption
            **common,
        )
    return None


def _descend_wrapper(root: _Root) -> _Root:
    """Some real ZIPs wrap everything in one top-level folder; step into it."""
    if (root / "your_instagram_activity").exists():
        return root
    subs = [d for d in root.iterdir() if d.is_dir()]
    if len(subs) == 1 and (subs[0] / "your_instagram_activity").exists():
        return subs[0]
    return root


def pull(
    settings: Settings, path: str | None = None, report: RunReport | None = None
) -> list[Item]:
    """Parse the DYI ZIP (or an already-extracted directory) -> normalized Items.

    Counts land in `report` (media_missing, per-skip errors) when core.run passes
    one; every skip is also logged. Raises on a missing path or an HTML-format
    export (JSON only in v1 — re-request the export as JSON).
    """
    if not path:
        raise ValueError("dyi_export needs path=<export .zip or extracted directory>")
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"export not found: {p}")
    report = report if report is not None else RunReport()
    root = _descend_wrapper(p if p.is_dir() else zipfile.Path(p))
    source_export = p.name if p.is_dir() else p.stem  # e.g. "instagram-2026-07-20"
    counts: Counter = Counter()

    # Self-thread items FIRST: a self-DM share and a saved entry of the same
    # permalink on the same UTC day share one id, and the ledger keeps the first
    # occurrence — the DM share carries the caption, the saved list never does.
    items: list[Item] = []
    for f in _self_thread_files(root, report):
        for msg in _load_json(f).get("messages", []):
            try:
                item = _message_item(msg, root, settings, source_export, counts, report)
            except Exception as exc:  # one crafted entry must not kill the run
                counts["skipped_entries"] += 1
                note = f"unparseable self-thread message ({exc!r}); entry skipped"
                logger.warning("dyi_export: %s", note)
                report.errors.append(f"dyi_export: {note}")
                continue
            if item is None:
                counts["skipped_entries"] += 1
                note = "self-thread message with no share/text/media; entry skipped"
                logger.warning("dyi_export: %s", note)
                report.errors.append(f"dyi_export: {note}")
            else:
                items.append(item)
    items.extend(_saved_items(root, source_export, counts, report))

    report.media_missing += counts["media_missing"]
    logger.info(
        "dyi_export: %d items from %s (%d entries skipped, %d media missing)",
        len(items),
        source_export,
        counts["skipped_entries"],
        counts["media_missing"],
    )
    return items
