"""F6 action review — collect from frontmatter, digest render, checkbox apply-back."""

from __future__ import annotations

from datetime import date

import frontmatter

from sift.review import action_items, digest


def _write_note(settings, key: str, items: list[dict], caption: str = "cap") -> None:
    captures = settings.vault_path / "Captures"
    captures.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post("body", caption=caption, action_items=items)
    (captures / f"note-{key}.md").write_text(
        frontmatter.dumps(post) + "\n", encoding="utf-8"
    )


def _note_items(settings, key: str) -> list[dict]:
    path = settings.vault_path / "Captures" / f"note-{key}.md"
    return frontmatter.load(path).metadata["action_items"]


class TestCollect:
    def test_collects_across_notes_with_source_binding(self, settings):
        _write_note(
            settings, "aaaa1111", [{"text": "call the bank", "confidence": 0.9}]
        )
        _write_note(settings, "bbbb2222", [{"text": "renew visa", "confidence": 0.5}])

        got = action_items.collect(settings)
        assert [(c.key, c.index, c.item.text) for c in got] == [
            ("aaaa1111", 0, "call the bank"),
            ("bbbb2222", 0, "renew visa"),
        ]

    def test_malformed_entry_skipped_not_fatal(self, settings):
        _write_note(
            settings,
            "aaaa1111",
            [{"due": "not-a-date"}, {"text": "good one", "confidence": 2.5}],
        )
        got = action_items.collect(settings)
        assert [c.item.text for c in got] == ["good one"]
        assert got[0].item.confidence == 1.0  # clamped by the model validator

    def test_non_sift_files_ignored(self, settings):
        captures = settings.vault_path / "Captures"
        captures.mkdir(parents=True, exist_ok=True)
        (captures / "random-note.md").write_text("just a note\n")
        assert action_items.collect(settings) == []


class TestDigestRender:
    def test_grouped_sorted_flagged_and_linked(self, settings):
        _write_note(
            settings,
            "aaaa1111",
            [
                {"text": "overdue thing", "due": date(2020, 1, 1), "confidence": 0.9},
                {"text": "future thing", "due": date(2199, 1, 1), "confidence": 0.8},
                {"text": "someday", "confidence": 0.4},
            ],
        )
        path = digest.refresh(settings)
        text = (settings.vault_path / "Action Review.md").read_text()
        assert path.endswith("Action Review.md")
        due = text.index("## Due")
        undated = text.index("## Undated")
        assert due < text.index("overdue thing") < text.index("future thing") < undated
        lines = {
            ln.split("] ", 1)[1].split(" —")[0]: ln
            for ln in text.splitlines()
            if ln.startswith("- [")
        }
        assert "**OVERDUE**" in lines["overdue thing"]
        assert "**OVERDUE**" not in lines["future thing"]
        assert text.index("someday") > undated
        assert "[[note-aaaa1111]]" in text
        assert "^sift-aaaa1111-0" in text
        assert "(90%)" in text

    def test_empty_vault_renders_placeholder(self, settings):
        digest.refresh(settings)
        text = (settings.vault_path / "Action Review.md").read_text()
        assert "No action-item candidates yet." in text


class TestApplyBack:
    def _digest_with_edits(
        self, settings, confirm: str = "", dismiss: str = ""
    ) -> None:
        digest.refresh(settings)
        path = settings.vault_path / "Action Review.md"
        text = path.read_text()
        if confirm:
            text = text.replace(f"- [ ] {confirm}", f"- [x] {confirm}")
        if dismiss:
            text = text.replace(f"- [ ] {dismiss}", f"- [-] {dismiss}")
        path.write_text(text, encoding="utf-8")

    def test_confirm_and_dismiss_update_note_frontmatter(self, settings):
        _write_note(
            settings,
            "aaaa1111",
            [
                {"text": "confirm me", "confidence": 0.9},
                {"text": "dismiss me", "confidence": 0.8},
                {"text": "leave me", "confidence": 0.7},
            ],
        )
        self._digest_with_edits(settings, confirm="confirm me", dismiss="dismiss me")

        digest.refresh(settings)
        statuses = [i["status"] for i in _note_items(settings, "aaaa1111")]
        assert statuses == ["confirmed", "dismissed", "pending"]

        text = (settings.vault_path / "Action Review.md").read_text()
        assert "- [x] confirm me" in text  # visible under Confirmed
        assert "dismiss me" not in text  # gone from the digest, kept in the note
        assert "- [ ] leave me" in text

    def test_unconfirming_a_confirmed_item_reverts_to_pending(self, settings):
        _write_note(
            settings,
            "aaaa1111",
            [{"text": "oops", "confidence": 0.9, "status": "confirmed"}],
        )
        digest.refresh(settings)
        path = settings.vault_path / "Action Review.md"
        path.write_text(path.read_text().replace("- [x] oops", "- [ ] oops"))

        digest.refresh(settings)
        assert _note_items(settings, "aaaa1111")[0]["status"] == "pending"

    def test_stale_marker_ignored_never_fatal(self, settings):
        _write_note(settings, "aaaa1111", [{"text": "real", "confidence": 0.9}])
        path = settings.vault_path / "Action Review.md"
        settings.vault_path.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "- [x] ghost item (90%) [[gone]] ^sift-deadbeef-0\n"
            "- [x] ghost index (90%) [[note-aaaa1111]] ^sift-aaaa1111-9\n"
        )
        digest.refresh(settings)  # no crash; real item re-rendered pending
        assert "- [ ] real" in (settings.vault_path / "Action Review.md").read_text()
