"""F6 action review — collect from frontmatter, digest render, checkbox apply-back."""

from __future__ import annotations

from datetime import date

import frontmatter

from sift.review import action_items, digest


def _write_note(
    settings,
    key: str,
    items: list[dict],
    caption: str = "cap",
    saved_at: date | str = "today",
    domain: str | None = None,
) -> None:
    """saved_at defaults to today so undated items land inside the board window;
    pass None to model an old/undatable post."""
    captures = settings.vault_path / "Captures"
    captures.mkdir(parents=True, exist_ok=True)
    meta: dict = {"caption": caption, "action_items": items}
    if saved_at == "today":
        saved_at = date.today()
    if saved_at is not None:
        meta["saved_at"] = saved_at
    if domain is not None:
        meta["domain"] = domain
    post = frontmatter.Post("body", **meta)
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
    def test_expired_dues_leave_the_board_but_stay_in_the_note(self, settings):
        _write_note(
            settings,
            "aaaa1111",
            [
                {"text": "passed deadline", "due": date(2020, 1, 1), "confidence": 0.9},
                {"text": "future thing", "due": date(2199, 1, 1), "confidence": 0.8},
            ],
        )
        path = digest.refresh(settings)
        text = (settings.vault_path / "Action Review.md").read_text()
        assert path.endswith("Action Review.md")
        assert "passed deadline" not in text  # off the board...
        assert "passed deadline" in str(_note_items(settings, "aaaa1111"))  # ...kept
        assert text.index("## Due") < text.index("future thing")
        assert "**OVERDUE**" not in text
        assert "1 older or expired candidates" in text
        assert "[[note-aaaa1111]]" in text and "^sift-aaaa1111-1" in text

    def test_undated_grouped_by_domain_then_priority(self, settings):
        _write_note(
            settings,
            "aaaa1111",
            [
                {"text": "enroll in course", "confidence": 0.3, "priority": "high"},
                {"text": "maybe watch talk", "confidence": 0.9, "priority": "low"},
            ],
            domain="education",
        )
        _write_note(
            settings,
            "bbbb2222",
            # No explicit priority -> confidence fallback (0.9 -> high).
            [{"text": "update resume", "confidence": 0.9}],
            domain="career",
        )
        digest.refresh(settings)
        text = (settings.vault_path / "Action Review.md").read_text()
        # Domains are sections; explicit priority beats the confidence fallback.
        career, education = text.index("## Career"), text.index("## Education")
        assert career < text.index("update resume") < education
        assert (
            education
            < text.index("### High", education)
            < text.index("enroll in course")
            < text.index("### Low", education)
            < text.index("maybe watch talk")
        )

    def test_stale_undated_items_hidden_by_the_saved_window(self, settings):
        _write_note(
            settings,
            "aaaa1111",
            [{"text": "years-old idea", "confidence": 0.9}],
            saved_at=date(2023, 1, 1),
        )
        _write_note(
            settings,
            "bbbb2222",
            [{"text": "no save date", "confidence": 0.9}],
            saved_at=None,
        )
        digest.refresh(settings)
        text = (settings.vault_path / "Action Review.md").read_text()
        assert "years-old idea" not in text
        assert "no save date" not in text  # undatable -> treated as stale
        assert "2 older or expired candidates" in text

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
