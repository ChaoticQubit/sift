"""dyi_export adapter — fully offline against the synthetic fixture ZIP (conftest)."""

from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

import frontmatter
import pytest

from sift import core
from sift.ingest import dyi_export
from sift.models import Medium, RunReport

DECODED_CAPTION = "It’s the best desk setup \U0001f36a — café vibes"


def _pull(settings, path):
    report = RunReport()
    return dyi_export.pull(settings, str(path), report), report


def test_parse_counts(settings, dyi_zip):
    items, report = _pull(settings, dyi_zip)
    # 2 saved posts + reel share + text note + 2 photo messages = 6;
    # 1 malformed saved entry skipped-and-counted, group thread ignored.
    assert len(items) == 6
    assert sum("skipped" in e for e in report.errors) == 1
    assert all(i.ingest_via.value == "dyi_export" for i in items)
    assert all(i.source_export == "instagram-fake-2026-07-20" for i in items)


def test_saved_posts_and_collection_mapping(settings, dyi_zip):
    items, _ = _pull(settings, dyi_zip)
    by_link = {i.permalink: i for i in items if i.permalink}
    reel = by_link["https://www.instagram.com/reel/FAKEREEL01/"]
    assert reel.medium is Medium.reel
    assert reel.saved_at == date(2024, 7, 15)
    assert reel.caption == ""  # the saved list genuinely carries no caption
    assert reel.saved_collection == "Recipes \U0001f36a"  # mojibake emoji decoded
    post = by_link["https://www.instagram.com/p/FAKEPOST02/"]
    assert post.medium is Medium.image  # /p/ -> image (carousel indistinguishable)
    assert post.raw["title"] == "café_baker"  # mojibake accent decoded
    assert post.saved_collection is None


def test_self_thread_parsed_group_thread_ignored(settings, dyi_zip):
    items, _ = _pull(settings, dyi_zip)
    share = next(i for i in items if i.permalink and "FAKESELF03" in i.permalink)
    assert share.medium is Medium.reel
    assert share.caption == DECODED_CAPTION  # share_text is the caption, demojibaked
    note = next(i for i in items if i.medium is Medium.text)
    assert note.caption == "remember: book flights before friday"
    assert note.permalink is None
    # the 3-participant decoy thread contributed nothing
    assert not any(i.permalink and "GROUPDECOY99" in i.permalink for i in items)
    assert not any("group chat" in i.caption for i in items)


def test_media_copied_vs_missing(settings, dyi_zip):
    items, report = _pull(settings, dyi_zip)
    photos = [i for i in items if i.medium is Medium.image and not i.permalink]
    assert len(photos) == 2
    present = next(i for i in photos if i.media_files)
    assert present.media_files == ["media/photo_present.jpg"]
    copied = settings.vault_path / "media" / "photo_present.jpg"
    assert copied.read_bytes().startswith(b"\xff\xd8")
    missing = next(i for i in photos if not i.media_files)
    assert missing.id != present.id
    assert report.media_missing == 1


def test_ids_stable_across_parses(settings, dyi_zip):
    first, _ = _pull(settings, dyi_zip)
    second, _ = _pull(settings, dyi_zip)
    assert [i.id for i in first] == [i.id for i in second]
    assert len({i.id for i in first}) == len(first)  # and unique


def test_extracted_directory_accepted(settings, dyi_zip, tmp_path):
    dest = tmp_path / "instagram-fake-2026-07-20"
    with zipfile.ZipFile(dyi_zip) as zf:
        zf.extractall(dest)
    items, _ = _pull(settings, dest)
    assert len(items) == 6
    assert all(i.source_export == "instagram-fake-2026-07-20" for i in items)


def test_html_export_refused(settings, tmp_path):
    zp = tmp_path / "instagram-html.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("your_instagram_activity/saved/saved_posts.html", "<html></html>")
    with pytest.raises(ValueError, match="format = JSON"):
        dyi_export.pull(settings, str(zp))


def test_missing_path_or_file_raises(settings, tmp_path):
    with pytest.raises(ValueError, match="path"):
        dyi_export.pull(settings)
    with pytest.raises(FileNotFoundError):
        dyi_export.pull(settings, str(tmp_path / "nope.zip"))


def test_end_to_end_core_run(settings, dyi_zip, fake_model):
    report = core.run("dyi_export", settings, path=str(dyi_zip))
    assert (report.processed, report.written, report.skipped) == (6, 6, 0)
    assert report.media_missing == 1

    posts = [frontmatter.loads(Path(p).read_text()) for p in report.notes]
    share = next(
        p
        for p in posts
        if p.get("source") == "https://www.instagram.com/reel/FAKESELF03/"
    )
    assert share["caption"] == DECODED_CAPTION  # caption in frontmatter
    assert f"```\n{DECODED_CAPTION}\n```" in share.content  # and fenced in the body
    assert share["ingest_via"] == "dyi_export"
    assert share["source_export"] == "instagram-fake-2026-07-20"
    collected = next(
        p
        for p in posts
        if p.get("source") == "https://www.instagram.com/reel/FAKEREEL01/"
    )
    assert collected["saved_collection"] == "Recipes \U0001f36a"

    # Re-run: the ledger skips everything, nothing rewritten.
    report2 = core.run("dyi_export", settings, path=str(dyi_zip))
    assert (report2.processed, report2.written, report2.skipped) == (6, 0, 6)
