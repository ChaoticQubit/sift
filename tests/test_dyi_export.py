"""dyi_export adapter — fully offline against the synthetic fixture ZIP (conftest)."""

from __future__ import annotations

import json
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


def _self_export(tmp_path, messages, participants=None, name="instagram-crafted"):
    """An extracted-directory export containing only a self thread."""
    root = tmp_path / name
    thread = root / "your_instagram_activity/messages/inbox/self_notes"
    thread.mkdir(parents=True)
    body = {"participants": participants or [{"name": "Me"}], "messages": messages}
    (thread / "message_1.json").write_text(json.dumps(body))
    return root


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


def test_duplicated_participant_self_thread_ingested(settings, tmp_path):
    """Real exports have also listed the self thread's one name TWICE; both shapes
    must ingest (finding: entire self-DM history silently dropped otherwise)."""
    root = _self_export(
        tmp_path,
        [{"sender_name": "Fake Me", "timestamp_ms": 1721260800000, "content": "note"}],
        participants=[{"name": "Fake Me"}, {"name": "Fake Me"}],
    )
    items, report = _pull(settings, root)
    assert [i.caption for i in items] == ["note"]
    assert not any("no self-DMs" in e for e in report.errors)


def test_zero_self_dms_logged_and_counted(settings, tmp_path):
    # inbox present but no thread matches the note-to-self shape
    root = _self_export(
        tmp_path,
        [{"sender_name": "Me", "timestamp_ms": 1, "content": "x"}],
        participants=[{"name": "Me"}, {"name": "Friend"}],
    )
    items, report = _pull(settings, root)
    assert items == []
    assert any("no self-DMs ingested" in e for e in report.errors)
    # inbox absent entirely
    bare = tmp_path / "bare-export"
    (bare / "your_instagram_activity").mkdir(parents=True)
    items, report = _pull(settings, bare)
    assert items == []
    assert any("no self-DMs to ingest" in e for e in report.errors)


def test_media_uri_traversal_refused(settings, tmp_path):
    """Security regression: a '../' or absolute uri in an extracted-directory
    export must never read a file outside the export root into the vault."""
    secret = tmp_path / "SECRET.txt"
    secret.write_bytes(b"outside-the-export")
    root = _self_export(
        tmp_path,
        [
            {
                "sender_name": "Me",
                "timestamp_ms": 1721300000000,
                "photos": [{"uri": "../SECRET.txt"}, {"uri": str(secret)}],
            }
        ],
    )
    items, report = _pull(settings, root)
    media_dir = settings.vault_path / "media"
    assert not media_dir.exists() or not list(media_dir.iterdir())
    assert report.media_missing == 2
    assert sum("refused" in e for e in report.errors) == 2
    assert len(items) == 1 and items[0].media_files == []  # counted, not dropped


def test_media_uri_directory_or_dotdot_does_not_crash(settings, tmp_path):
    """Security regression: a uri naming a directory (or ending in '..') must be
    skipped-and-counted, not abort the whole ingest with IsADirectoryError."""
    root = _self_export(
        tmp_path,
        [
            {
                "sender_name": "Me",
                "timestamp_ms": 1721300000000,
                "photos": [
                    {"uri": "your_instagram_activity/messages/inbox/self_notes/photos"},
                    {
                        "uri": "your_instagram_activity/messages/inbox/self_notes/photos/.."
                    },
                ],
            }
        ],
    )
    photos = root / "your_instagram_activity/messages/inbox/self_notes/photos"
    photos.mkdir()
    (photos / "real.jpg").write_bytes(b"\xff\xd8")
    items, report = _pull(settings, root)  # completes — no exception
    assert report.media_missing == 2
    assert len(items) == 1 and items[0].media_files == []


def test_self_share_caption_wins_id_collision(settings, tmp_path, fake_model):
    """Save + self-DM the same reel -> one id (permalink-keyed, date-free — F2 dedup).
    The caption-bearing DM share must be the occurrence the ledger keeps
    (captions are first-class)."""
    link = "https://www.instagram.com/reel/SAMEDAY01/"
    zp = tmp_path / "instagram-collide.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr(
            "your_instagram_activity/saved/saved_posts.json",
            json.dumps(
                {
                    "saved_saved_media": [
                        {
                            "title": "poster",
                            "string_map_data": {
                                "Saved on": {"href": link, "timestamp": 1721001600}
                            },
                        }
                    ]
                }
            ),
        )
        zf.writestr(
            "your_instagram_activity/messages/inbox/self_notes/message_1.json",
            json.dumps(
                {
                    "participants": [{"name": "Me"}],
                    "messages": [
                        {
                            "sender_name": "Me",
                            "timestamp_ms": 1721005200000,  # same UTC day
                            "share": {"link": link, "share_text": "the real caption"},
                        }
                    ],
                }
            ),
        )
    items, _ = _pull(settings, zp)
    assert len(items) == 2 and items[0].id == items[1].id
    assert items[0].caption == "the real caption"  # DM share emitted first
    report = core.run("dyi_export", settings, path=str(zp))
    assert (report.written, report.skipped) == (1, 1)
    note = frontmatter.loads(Path(report.notes[0]).read_text())
    assert note["caption"] == "the real caption"


def _new_shape_export(tmp_path) -> Path:
    """The 2025+ export schema (verified against a real 2026-07 export): bare
    top-level lists, label_values rows, captions present in the saved list,
    collection members nested under "dict" entries."""
    root = tmp_path / "instagram-new-2026-07"
    saved = root / "your_instagram_activity/saved"
    saved.mkdir(parents=True)
    reel = "https://www.instagram.com/reel/NEWSHAPE1/"
    (saved / "saved_posts.json").write_text(
        json.dumps(
            [
                {
                    "timestamp": 1784005125,
                    "media": [],
                    "label_values": [
                        {"label": "URL", "value": reel, "href": reel},
                        # mojibake: latin-1-escaped UTF-8 for "café"
                        {"label": "Caption", "value": "cafÃ© recipe"},
                        {"label": "Title", "value": "cookwithme"},
                        {"timestamp_value": 1784005125},
                    ],
                },
                {
                    "timestamp": 1,
                    "label_values": [{"label": "Caption", "value": "no url row"}],
                },
            ]
        )
    )
    (saved / "saved_collections.json").write_text(
        json.dumps(
            [
                {
                    "timestamp": 1751414145,
                    "label_values": [
                        {"label": "Name", "value": "Food"},
                        {"label": "Type", "value": "Default"},
                        {
                            "dict": [
                                {
                                    "dict": [
                                        # /p/ form of the same shortcode: the map
                                        # must still hit the /reel/ saved row.
                                        {
                                            "label": "URL",
                                            "href": "https://www.instagram.com/p/NEWSHAPE1/",
                                        },
                                        {"label": "Caption", "value": "member cap"},
                                    ]
                                }
                            ]
                        },
                    ],
                }
            ]
        )
    )
    return root


def test_new_shape_saved_posts_with_captions(settings, tmp_path):
    items, report = _pull(settings, _new_shape_export(tmp_path))
    [item] = items
    assert item.permalink == "https://www.instagram.com/reel/NEWSHAPE1/"
    assert item.medium is Medium.reel
    assert item.caption == "café recipe"  # captions exist in the 2025+ shape
    assert item.saved_at == date(2026, 7, 14)
    assert item.saved_collection == "Food"  # nested member, canonicalized match
    assert item.raw["title"] == "cookwithme"
    assert sum("skipped" in e for e in report.errors) == 1  # the url-less row


def test_new_shape_ids_dedup_with_legacy(settings, tmp_path):
    """Same shortcode via either schema -> same Item.id (canonical, date-free)."""
    items, _ = _pull(settings, _new_shape_export(tmp_path))
    legacy_id = dyi_export._item_id(
        dyi_export.canonical_permalink("https://www.instagram.com/p/NEWSHAPE1/"), ""
    )
    assert items[0].id == legacy_id


def test_wrapped_zip_descends(settings, dyi_zip, tmp_path):
    """A ZIP that wraps everything in one top-level folder still parses."""
    wrapped = tmp_path / "wrapped.zip"
    with zipfile.ZipFile(dyi_zip) as src, zipfile.ZipFile(wrapped, "w") as dst:
        for info in src.infolist():
            dst.writestr("wrapper/" + info.filename, src.read(info.filename))
    items, _ = _pull(settings, wrapped)
    assert len(items) == 6


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
