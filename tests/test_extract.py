"""F3 extractors — OCR loop, guarded link fetch, caption pass-through, router dispatch.

Fully offline: the VLM seam (images_ocr._run) and the fetch (links.guarded_fetch)
are monkeypatched (CLAUDE.md Conventions).
"""

from __future__ import annotations

import base64
import shutil
from types import SimpleNamespace

import pytest

from sift.agents import interpreter
from sift.extract import captions, images_ocr, links, router
from sift.guardrails.egress import EgressError
from sift.models import Extraction, IngestVia, Item, Medium


def _item(**kw) -> Item:
    defaults = dict(
        id="item-1",
        permalink="https://www.instagram.com/p/FAKE01/",
        medium=Medium.image,
        caption="a caption",
        ingest_via=IngestVia.dyi_export,
    )
    return Item(**{**defaults, **kw})


def _put_image(settings, name: str) -> str:
    media = settings.vault_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / name).write_bytes(b"\x89PNG fake-bytes")
    return f"media/{name}"


class TestImagesOcr:
    def test_loops_all_images_and_concatenates(self, settings, monkeypatch):
        files = [_put_image(settings, "a.png"), _put_image(settings, "b.jpg")]
        texts = iter(["first slide", "second slide"])
        seen: list[str] = []

        def fake_run(agent, image):
            seen.append(str(image.filepath))
            return SimpleNamespace(content=next(texts))

        monkeypatch.setattr("sift.extract.images_ocr._run", fake_run)
        out = images_ocr.extract(
            _item(medium=Medium.carousel, media_files=files), settings
        )
        assert out.text == "first slide\n\nsecond slide"
        assert len(seen) == 2  # loop ALL N (DESIGN.md §7)

    def test_missing_and_non_image_files_skipped(self, settings, monkeypatch):
        files = [
            "media/gone.png",  # referenced but absent
            _put_image(settings, "clip.mp4"),  # not a VLM-supported image
            _put_image(settings, "real.webp"),
        ]
        monkeypatch.setattr(
            "sift.extract.images_ocr._run",
            lambda a, i: SimpleNamespace(content="from real.webp"),
        )
        out = images_ocr.extract(_item(media_files=files), settings)
        assert out.text == "from real.webp"

    def test_vlm_failure_skips_image_not_item(self, settings, monkeypatch):
        files = [_put_image(settings, "a.png"), _put_image(settings, "b.png")]
        results = iter([RuntimeError("api down"), SimpleNamespace(content="ok")])

        def fake_run(agent, image):
            r = next(results)
            if isinstance(r, Exception):
                raise r
            return r

        monkeypatch.setattr("sift.extract.images_ocr._run", fake_run)
        out = images_ocr.extract(_item(media_files=files), settings)
        assert out.text == "ok"

    def test_no_images_yields_empty_extraction(self, settings):
        out = images_ocr.extract(_item(media_files=[]), settings)
        assert out.text == ""  # interpret() falls back to the caption


class TestLocalOcrBackend:
    def test_local_backend_calls_local_seam_not_agent(self, settings, monkeypatch):
        settings.ocr_backend = "local"
        files = [_put_image(settings, "a.png"), _put_image(settings, "b.jpg")]
        calls: list[str] = []

        def fake_local(s, image_bytes, mime):
            calls.append(mime)
            return f"text via {s.ocr_model}"

        monkeypatch.setattr("sift.extract.images_ocr._run_local", fake_local)
        monkeypatch.setattr(
            "sift.extract.images_ocr.build_agent",
            lambda s: pytest.fail("provider agent built on local backend"),
        )
        out = images_ocr.extract(
            _item(medium=Medium.carousel, media_files=files), settings
        )
        assert calls == ["image/png", "image/jpeg"]
        assert "text via" in out.text

    def test_webp_converted_to_png_for_local_server(self, settings, monkeypatch):
        """The local OCR server 400s on webp; the loop must convert, never send raw."""
        settings.ocr_backend = "local"
        files = [_put_image(settings, "chart.webp"), _put_image(settings, "a.png")]
        sent: list[tuple[bytes, str]] = []
        monkeypatch.setattr(
            "sift.extract.images_ocr._png_bytes", lambda p: b"converted-png"
        )
        monkeypatch.setattr(
            "sift.extract.images_ocr._run_local",
            lambda s, b, m: sent.append((b, m)) or "ok",
        )
        images_ocr.extract(_item(medium=Medium.carousel, media_files=files), settings)
        assert sent[0] == (b"converted-png", "image/png")  # webp never sent raw
        assert sent[1] == (b"\x89PNG fake-bytes", "image/png")  # others untouched

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
    def test_png_bytes_really_converts_webp(self, tmp_path):
        # A minimal valid 1x1 VP8L webp, inlined: local ffmpeg builds decode webp
        # natively but often lack an ENCODER, so we can't generate one on the fly.
        src = tmp_path / "real.webp"
        src.write_bytes(
            base64.b64decode("UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA==")
        )
        assert images_ocr._png_bytes(src)[:8] == b"\x89PNG\r\n\x1a\n"

    def test_local_ocr_request_caps_generation_and_times_out(self, settings, monkeypatch):
        """A degenerate loop generates unbounded output server-side; the request
        must cap generation (and keep its socket timeout) or analysis hangs for
        hours on a response that never finishes (observed 2026-07-26)."""
        import io
        import json as _json

        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["body"] = _json.loads(request.data)
            captured["timeout"] = timeout
            return io.BytesIO(
                _json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode()
            )

        monkeypatch.setattr(
            "sift.extract.images_ocr.urllib.request.urlopen", fake_urlopen
        )
        assert images_ocr._run_local(settings, b"png-bytes", "image/png") == "hi"
        assert captured["body"]["max_tokens"] == 2000
        assert captured["timeout"] == 300

    def test_degenerate_ocr_loops_are_dropped_not_written(self, settings, monkeypatch):
        """Unlimited-OCR spirals on textless frames, repeating a meta sentence
        dozens of times; that junk must never reach a note (2026-07-25, 18 notes)."""
        settings.ocr_backend = "local"
        files = [_put_image(settings, "blank.png"), _put_image(settings, "list.png")]
        junk = "The OCR result is inconsistent with the GT's visual content. " * 12
        results = iter([junk, "A real list of books shown on screen\nDDIA\nSICP"])
        monkeypatch.setattr(
            "sift.extract.images_ocr._run_local", lambda s, b, m: next(results)
        )
        out = images_ocr.extract(_item(media_files=files), settings)
        assert "GT's visual content" not in out.text  # junk frame dropped
        assert "DDIA" in out.text  # real screen text kept

    def test_local_failure_skips_image_not_item(self, settings, monkeypatch):
        settings.ocr_backend = "local"
        files = [_put_image(settings, "a.png"), _put_image(settings, "b.png")]
        results = iter([OSError("server down"), "recovered"])

        def fake_local(s, image_bytes, mime):
            r = next(results)
            if isinstance(r, Exception):
                raise r
            return r

        monkeypatch.setattr("sift.extract.images_ocr._run_local", fake_local)
        out = images_ocr.extract(_item(media_files=files), settings)
        assert out.text == "recovered"


class TestLinks:
    HTML = (
        "<html><head><title>Sourdough Guide</title></head><body><main>"
        "<p>Feed the starter twice a day until it doubles reliably.</p>"
        "<p>Then bake at 250C in a dutch oven for best spring.</p>"
        "</main></body></html>"
    )

    def test_readable_text_and_title(self, settings, monkeypatch):
        url = "https://blog.example.com/sourdough"
        monkeypatch.setattr(
            "sift.extract.links.guarded_fetch",
            lambda u, **kw: SimpleNamespace(status=200, data=self.HTML.encode()),
        )
        out = links.extract(_item(medium=Medium.link, permalink=url), settings)
        assert "Sourdough Guide" in out.text
        assert "Feed the starter" in out.text
        assert out.artifacts["url"] == url
        assert out.artifacts["title"] == "Sourdough Guide"

    def test_egress_refusal_is_caption_only_not_fatal(self, settings, monkeypatch):
        def refuse(u, **kw):
            raise EgressError("non-public address")

        monkeypatch.setattr("sift.extract.links.guarded_fetch", refuse)
        out = links.extract(
            _item(medium=Medium.link, permalink="https://internal.example/x"), settings
        )
        assert out.text == ""

    def test_http_error_is_caption_only(self, settings, monkeypatch):
        monkeypatch.setattr(
            "sift.extract.links.guarded_fetch",
            lambda u, **kw: SimpleNamespace(status=404, data=b""),
        )
        out = links.extract(_item(medium=Medium.link), settings)
        assert out.text == ""

    def test_no_permalink_is_caption_only(self, settings):
        out = links.extract(_item(medium=Medium.link, permalink=None), settings)
        assert out.text == ""


class TestCaptionsAndRouter:
    def test_caption_passthrough_verbatim(self):
        out = captions.extract(_item(medium=Medium.text, caption="note to self"))
        assert out.text == "note to self"

    @pytest.mark.parametrize(
        ("medium", "target"),
        [
            (Medium.image, "sift.extract.images_ocr.extract"),
            (Medium.carousel, "sift.extract.images_ocr.extract"),
            (Medium.link, "sift.extract.links.extract"),
        ],
    )
    def test_router_dispatch(self, settings, monkeypatch, medium, target):
        marker = Extraction(text=f"via {target}")
        monkeypatch.setattr(target, lambda item, s: marker)
        assert router.extract(_item(medium=medium), settings) is marker


class TestInterpreterSeesCaptionAndExtraction:
    def test_both_reach_the_model(self, settings, fake_model):
        interpreter.interpret(
            _item(caption="the caption"), Extraction(text="ocr text"), settings
        )
        assert "the caption" in fake_model.prompts[0]
        assert "ocr text" in fake_model.prompts[0]

    def test_caption_echo_sent_once(self, settings, fake_model):
        interpreter.interpret(
            _item(caption="same text"), Extraction(text="same text"), settings
        )
        assert fake_model.prompts[0].count("same text") == 1
