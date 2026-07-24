"""Adapter: capture-at-save-time via Share-Sheet / Shortcut. Zero ban risk, real-time. DESIGN.md §4.

Replaces the 'DM myself' habit: user taps Share -> Sift, item lands instantly.
docs/share-capture.md owns the phone/browser setup.

Transport: a tiny stdlib HTTP endpoint (`sift serve`) accepts a shared URL and/or a
typed note, durably stashes it as one JSON file in <work_dir>/share_inbox/, then runs
the normal pipeline. The inbox is the capture/pipeline seam: a share is never lost to
a crash (the file leaves the inbox only after its item is durably in the dedup ledger,
moved to share_inbox/processed/ on a later drain), and a share captured while the
model/embedder is unreachable still becomes a note on the next run.

Trust boundary (exempt from simplification — CLAUDE.md gate 5): the endpoint takes
input from the LAN. Token required (constant-time compare) unless bound to loopback;
serving on a non-loopback host WITHOUT a token is refused at startup. Bodies capped,
JSON only, URL scheme must be http(s). Shared ids reuse dyi_export's canonical-permalink
scheme so the same post captured here and later fetched dedups to one note.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
import time
import uuid
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..config import Settings
from ..models import IngestVia, Item, Medium, RunReport
from .dyi_export import _infer_medium, _item_id, canonical_permalink

logger = logging.getLogger(__name__)

_MAX_BODY = 64 * 1024  # a URL + a typed note; anything bigger is not a share
_TEXT_CAP = 10_000


# --- inbox -> Items (the ingest adapter) ---------------------------------------------


def _inbox(settings: Settings) -> Path:
    return settings.resolved_work_dir / "share_inbox"


def _to_item(payload: dict) -> Item:
    url = (payload.get("url") or "").strip()
    text = str(payload.get("text") or "").strip()[:_TEXT_CAP]
    ts = str(payload.get("ts") or "")
    saved = None
    try:
        saved = datetime.fromtimestamp(float(ts), tz=timezone.utc).date()
    except (TypeError, ValueError):
        saved = date.today()
    if url:
        return Item(
            id=_item_id(canonical_permalink(url), ""),
            permalink=url,
            medium=_infer_medium(url),
            caption=text,  # the user's typed note rides as the caption
            ingest_via=IngestVia.share_capture,
            saved_at=saved,
        )
    return Item(
        id=_item_id("", f"{text}{ts}"),
        medium=Medium.text,
        caption=text,
        ingest_via=IngestVia.share_capture,
        saved_at=saved,
    )


def pull(
    settings: Settings, path: str | None = None, report: RunReport | None = None
) -> list[Item]:
    """Drain the capture inbox -> Items. ingest_via=share_capture.

    A file leaves the inbox only after its item is in the dedup ledger (i.e. its note
    landed on an earlier run) — moved to processed/ here, so a crash between parse and
    write never loses a share. Corrupt files go to bad/ with a logged report error.
    """
    from ..db import storage

    report = report if report is not None else RunReport()
    inbox = _inbox(settings)
    items: list[Item] = []
    for f in sorted(inbox.glob("*.json")):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
            item = _to_item(payload)
        except Exception as exc:
            bad = inbox / "bad"
            bad.mkdir(parents=True, exist_ok=True)
            f.rename(bad / f.name)
            msg = f"share_capture: corrupt inbox file {f.name} ({exc}); moved to bad/"
            logger.warning(msg)
            report.errors.append(msg)
            continue
        if not item.caption and not item.permalink:
            bad = inbox / "bad"
            bad.mkdir(parents=True, exist_ok=True)
            f.rename(bad / f.name)
            msg = f"share_capture: empty capture {f.name}; moved to bad/"
            logger.warning(msg)
            report.errors.append(msg)
            continue
        if not storage.is_new(item.id, settings):
            processed = inbox / "processed"
            processed.mkdir(parents=True, exist_ok=True)
            f.rename(processed / f.name)  # landed on an earlier run; archive
            continue
        items.append(item)
    return items


# --- capture endpoint (`sift serve`) -------------------------------------------------


def stash(settings: Settings, url: str, text: str) -> Path:
    """Validate one capture and write it durably into the inbox (atomic rename)."""
    url, text = url.strip(), text.strip()[:_TEXT_CAP]
    if url:
        split = urlsplit(url)
        if split.scheme not in ("http", "https") or not split.hostname:
            raise ValueError("url must be http(s)")
    if not url and not text:
        raise ValueError("nothing to capture: need url and/or text")
    inbox = _inbox(settings)
    inbox.mkdir(parents=True, exist_ok=True)
    name = f"{int(time.time())}-{uuid.uuid4().hex[:8]}.json"
    tmp = inbox / f".{name}.tmp"
    tmp.write_text(
        json.dumps({"url": url, "text": text, "ts": time.time()}), encoding="utf-8"
    )
    final = inbox / name
    tmp.rename(final)
    return final


def _token_ok(settings: Settings, presented: str) -> bool:
    if not settings.capture_token:
        return True  # loopback-only mode; serve() refuses this on a LAN bind
    return hmac.compare_digest(settings.capture_token, presented)


class _Handler(BaseHTTPRequestHandler):
    settings: Settings  # set by serve()
    run_lock = threading.Lock()

    def log_message(self, fmt: str, *args) -> None:  # route to logging, not stderr
        logger.info("capture endpoint: " + fmt, *args)

    def _respond(self, code: int, body: str, ctype: str = "text/plain") -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _capture(self, token: str, url: str, text: str) -> None:
        if not _token_ok(self.settings, token):
            self._respond(403, "forbidden")
            return
        try:
            stash(self.settings, url, text)
        except ValueError as exc:
            self._respond(400, str(exc))
            return
        # Respond first, process after: the share is durable in the inbox; the phone
        # gets instant feedback instead of waiting out a model call.
        self._respond(200, "✓ Captured to Sift", ctype="text/html")
        threading.Thread(target=self._process, daemon=True).start()

    def _process(self) -> None:
        from .. import core

        if not self.run_lock.acquire(blocking=False):
            return  # a run is active; it (or the next drain) picks the item up
        try:
            core.run("share_capture", self.settings)
        except Exception as exc:
            logger.warning(
                "capture endpoint: pipeline run failed (%s); item stays inboxed", exc
            )
        finally:
            self.run_lock.release()

    def do_GET(self) -> None:  # bookmarklet / Shortcut "Get URL" path
        split = urlsplit(self.path)
        if split.path != "/capture":
            self._respond(404, "not found")
            return
        q = parse_qs(split.query)
        self._capture(
            token=(q.get("token") or [""])[0],
            url=(q.get("url") or [""])[0],
            text=(q.get("text") or [""])[0],
        )

    def do_POST(self) -> None:  # Shortcut JSON path
        if urlsplit(self.path).path != "/capture":
            self._respond(404, "not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > _MAX_BODY:
            self._respond(413, "too large")
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("body must be a JSON object")
        except Exception:
            self._respond(400, "invalid JSON")
            return
        self._capture(
            token=self.headers.get("X-Sift-Token", "")
            or str(payload.get("token") or ""),
            url=str(payload.get("url") or ""),
            text=str(payload.get("text") or ""),
        )


def make_server(settings: Settings, host: str, port: int) -> ThreadingHTTPServer:
    """Build the endpoint server; refuses a non-loopback bind without a token."""
    if host not in ("127.0.0.1", "localhost", "::1") and not settings.capture_token:
        raise SystemExit(
            "refusing to serve beyond loopback without SIFT_CAPTURE_TOKEN set "
            "(anyone on the network could write into your vault)"
        )
    handler = type("BoundHandler", (_Handler,), {"settings": settings})
    return ThreadingHTTPServer((host, port), handler)


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8595) -> None:
    server = make_server(settings, host, port)
    logger.info("capture endpoint on http://%s:%d/capture", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
