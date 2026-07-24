"""F7 share_capture — inbox drain, cross-adapter dedup ids, endpoint trust boundary.

Offline: the endpoint runs on an ephemeral loopback port; the pipeline hook is
monkeypatched (CLAUDE.md Conventions).
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from urllib.parse import quote

import pytest

from sift.db import storage
from sift.ingest import share_capture
from sift.ingest.dyi_export import _item_id, canonical_permalink
from sift.models import Medium, RunReport


def _stash_raw(settings, payload: dict, name: str = "1-abc.json") -> None:
    inbox = settings.resolved_work_dir / "share_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_text(json.dumps(payload), encoding="utf-8")


class TestPull:
    def test_url_share_dedups_with_export_id(self, settings):
        # The same reel shared here and present in a DYI export must be ONE id.
        url = "https://www.instagram.com/reel/ABC123/?igsh=junk"
        _stash_raw(settings, {"url": url, "text": "great recipe", "ts": time.time()})

        [item] = share_capture.pull(settings)
        assert item.id == _item_id(canonical_permalink(url), "")
        assert item.medium is Medium.reel
        assert item.caption == "great recipe"
        assert item.ingest_via.value == "share_capture"

    def test_text_only_share_becomes_text_item(self, settings):
        _stash_raw(settings, {"text": "idea: build the thing", "ts": 1700000000.0})
        [item] = share_capture.pull(settings)
        assert item.medium is Medium.text
        assert item.caption == "idea: build the thing"
        assert item.permalink is None

    def test_corrupt_and_empty_files_quarantined_with_report(self, settings):
        _stash_raw(settings, {"url": "https://example.com/a"}, "2-ok.json")
        inbox = settings.resolved_work_dir / "share_inbox"
        (inbox / "1-bad.json").write_text("{not json", encoding="utf-8")
        (inbox / "3-empty.json").write_text("{}", encoding="utf-8")

        report = RunReport()
        items = share_capture.pull(settings, report=report)
        assert [i.permalink for i in items] == ["https://example.com/a"]
        assert len(report.errors) == 2
        assert sorted(p.name for p in (inbox / "bad").iterdir()) == [
            "1-bad.json",
            "3-empty.json",
        ]

    def test_ledger_seen_file_archived_not_reprocessed(self, settings):
        url = "https://www.instagram.com/p/SEEN01/"
        _stash_raw(settings, {"url": url}, "1-seen.json")
        storage.mark_seen(_item_id(canonical_permalink(url), ""), settings)

        assert share_capture.pull(settings) == []
        inbox = settings.resolved_work_dir / "share_inbox"
        assert (inbox / "processed" / "1-seen.json").exists()
        assert not (inbox / "1-seen.json").exists()

    def test_unseen_file_stays_in_inbox_until_landed(self, settings):
        _stash_raw(settings, {"url": "https://example.com/new"}, "1-new.json")
        share_capture.pull(settings)
        # Crash-safety: still inboxed (only a ledger entry moves it to processed/).
        assert (settings.resolved_work_dir / "share_inbox" / "1-new.json").exists()


class TestStash:
    def test_rejects_non_http_url(self, settings):
        with pytest.raises(ValueError, match="http"):
            share_capture.stash(settings, "javascript:alert(1)", "")

    def test_rejects_empty_capture(self, settings):
        with pytest.raises(ValueError, match="nothing to capture"):
            share_capture.stash(settings, "", "   ")

    def test_writes_durable_inbox_file(self, settings):
        path = share_capture.stash(settings, "https://example.com/x", "note")
        data = json.loads(path.read_text())
        assert (data["url"], data["text"]) == ("https://example.com/x", "note")


@pytest.fixture
def endpoint(settings, monkeypatch):
    """A live loopback endpoint with the pipeline hook recorded, not run."""
    runs: list[str] = []
    monkeypatch.setattr(
        "sift.core.run", lambda source, s, path=None: runs.append(source)
    )
    settings.capture_token = "sekrit"
    server = share_capture.make_server(settings, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base, runs
    server.shutdown()
    server.server_close()


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


class TestEndpoint:
    def test_get_capture_with_token(self, settings, endpoint):
        base, runs = endpoint
        status, body = _get(
            f"{base}/capture?token=sekrit&url="
            + quote("https://www.instagram.com/p/XYZ/")
        )
        assert status == 200 and "Captured" in body
        for _ in range(50):  # the pipeline hook fires on a worker thread
            if runs:
                break
            time.sleep(0.02)
        assert runs == ["share_capture"]
        assert list((settings.resolved_work_dir / "share_inbox").glob("*.json"))

    def test_wrong_token_403_and_nothing_written(self, settings, endpoint):
        base, _ = endpoint
        status, _body = _get(f"{base}/capture?token=wrong&url=https://example.com")
        assert status == 403
        inbox = settings.resolved_work_dir / "share_inbox"
        assert not (inbox.exists() and list(inbox.glob("*.json")))

    def test_post_json_capture(self, settings, endpoint):
        base, _ = endpoint
        req = urllib.request.Request(
            f"{base}/capture",
            data=json.dumps({"text": "typed note", "token": "sekrit"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
        [f] = (settings.resolved_work_dir / "share_inbox").glob("*.json")
        assert json.loads(f.read_text())["text"] == "typed note"

    def test_bad_url_400_unknown_path_404(self, settings, endpoint):
        base, _ = endpoint
        assert _get(f"{base}/capture?token=sekrit&url=ftp://x")[0] == 400
        assert _get(f"{base}/other")[0] == 404


def test_lan_bind_without_token_refused(settings):
    settings.capture_token = None
    with pytest.raises(SystemExit, match="SIFT_CAPTURE_TOKEN"):
        share_capture.make_server(settings, "0.0.0.0", 0)
