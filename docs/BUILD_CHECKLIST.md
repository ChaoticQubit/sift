# Sift — Build Checklist

Phased, checkbox tasks for Claude Code. Ship each phase before the next. ⭐ = spike-first. Read `DESIGN.md` + `AGENTS.md` for the why. `[S]/[M]/[L]` = size.

---

## F0 — Foundation `[M]`  *(no Instagram, no untrusted content yet)*
- [ ] `pyproject.toml` — core deps light (`agno`, `gallery-dl`, `yt-dlp`, `trafilatura`, `dateparser`, `typer`, `pydantic*`, `python-frontmatter`); heavy deps in extras: `[guardrails]` (presidio, llm-guard), `[stt]` (faster-whisper), `[rag]` (lancedb), `[ocr]` (F3); `[dev]`: pytest/ruff/black. `.gitignore`, `.env.example`.
- [ ] `config.py` — pydantic-settings: `SIFT_VAULT_PATH`, `SIFT_WORK_DIR` (default `<vault>/.sift`), `SIFT_DB_URL` (sqlite), LLM provider+key, embedder, feature flags. No instagrapi flag — the ban is a rule (CLAUDE.md 1), not a toggle.
- [ ] `models.py` — `Item` (incl. `caption`, `ingest_via`), `Extraction`, `Interpretation`, `ActionItem`, `Note`, `RunReport`.
- [ ] `db/storage.py` — Agno `Db` on SQLite (sessions + memory); dedup ledger.
- [ ] `vault/frontmatter.py` + `vault/writer.py` — §6 schema (incl. `caption`), idempotent write, classification routing.
- [ ] `agents/` — a minimal Agno agent wired to storage (hello-world) to prove the framework + version pin.
- [ ] `core.run(source, config) -> RunReport` skeleton; `cli.py` (typer) thin shells.
- [ ] `tests/fixtures/` — fake DYI export (saved_posts + self-thread + 1 image + 1 short mp4) with captions.
- [ ] ⭐ **Dry run:** a fixture item → correct note (caption populated), idempotent. *Done.*

## F0.5 — Guardrail harness `[M]`  dep: F0  *(do this before any untrusted content)*
- [ ] `guardrails/hooks.py` — the interpreter's `pre_hooks`/`post_hooks` wiring (order cheap→expensive).
- [ ] `guardrails/injection.py` — Agno `PromptInjectionGuardrail` + `UntrustedContentGuardrail` (spotlight/delimit, strip `U+E0000–U+E007F`, sanitize md links) — deterministic sanitize usable outside hooks too.
- [ ] `guardrails/pii.py` — Agno `PIIDetectionGuardrail(mask=True)` + `PresidioGuardrail(BaseGuardrail)` on LLM-bound text; output `SensitiveOutputGuardrail` (LLM Guard). Vault stays verbatim (AGENTS.md §3a).
- [ ] `guardrails/egress.py` — fetcher policy: credential-less link fetch, private/loopback-IP block (SSRF guard); gallery-dl/yt-dlp scoped to instagram.com + CDNs.
- [ ] `agents/interpreter.py` — the quarantined **tool-less** interpreter skeleton: strict `Interpretation` output schema, guardrail hooks attached, NO tools, NO secrets — the **Rule of Two** (AGENTS.md §2).
- [ ] ⭐ **Guardrail tests:** a caption containing an injection payload + a fake SSN/email is neutralized (blocked/masked) on the LLM-bound path, and off-schema interpreter output is rejected. *Done.* See `AGENTS.md`.

## F1 — Ingest: DYI export + captions `[M]`  dep: F0
- [ ] `ingest/dyi_export.py` — parse ZIP: saved posts, saved collections (names), self-DM/note-to-self thread, **captions**. JSON + HTML shapes. Stable `Item.id`.
- [ ] Wire into `core.run(source="dyi_export", path=...)`. *Done = your export backfills notes; captions populated; self-DMs included.*

## F2 — Ingest: cookie_fetch + captions `[L]`  dep: F1
- [ ] `ingest/cookie_fetch.py` — `gallery-dl --cookies-from-browser` on the `saved` extractor; caption via metadata postprocessor; `--download-archive` incremental; **read-only**; `sleep-request>=3s`; single session; **429 back-off (pause days)**. Playwright fallback stub.
- [ ] Weekly scheduler (cron/launchd) doc + `sift run --source cookie_fetch`. *Done = a scheduled run pulls only NEW saves, read-only, on your machine/home IP, backs off on 429.*

## F3 — Extract + interpret `[L]`  dep: F0.5, F1
- [ ] `extract/captions.py` (first-class), `extract/images_ocr.py` (loop N; PaddleOCR + VLM fallback), `extract/links.py` (trafilatura, credential-less, private-IP block) — invoked by `extract/router.py` as **deterministic pipeline code**, not agent tools.
- [ ] `agents/interpreter.py` — full implementation: sanitized text → classify + summarize + tags + candidate action items (strict `Interpretation` schema, reasoning on); deterministic writer consumes the validated record → `Note`.
- [ ] *Done = a real carousel and a real link post become good notes; caption analyzed.*

## F4 — Reels / video STT `[M]`  dep: F3
- [ ] `extract/reels_stt.py` — ffmpeg → freeflow/faster-whisper; language detect + confidence; flag low-confidence; no translation beyond X→English (off by default). *Done.*

## F5 — RAG search `[M]`  dep: F3
- [ ] `rag/knowledge.py` — Agno Knowledge over LanceDB (hybrid); incremental. `rag/search.py` + `sift search`. *Done = find a note by content.*

## F6 — Action review `[M]`  dep: F3
- [ ] `review/action_items.py` (dateparser normalize) + `review/digest.py` (`Action Review.md`, grouped, links back, `status: pending`; confirm/dismiss updates frontmatter). *Done = a real deadline surfaces; nothing fires unconfirmed.*

## F7 — share_capture `[M]`  dep: F0.5
- [ ] `ingest/share_capture.py` + a small capture endpoint; iOS/Android Share-Sheet/Shortcut + browser action → writes shared posts + notes into Sift. *Done = tapping Share files a note in real time; replaces self-DM.*

---

## v-next (DEFERRED)
- [ ] `extract/frames_ocr.py` (keyframe dedup + OCR). [ ] Non-English target translation. [ ] Auto-linking. [ ] Meta Data Portability API adapter (only if Sift incorporates / US opens). [ ] Fold into Life Planner (scheduled Agno agent + dashboard + confirmed items → planner).

## Guardrails (check every phase)
- [ ] No `instagrapi`; account code is read-only, weekly, one session, 429 back-off.
- [ ] Rule-of-Two intact: the interpreter (the only model reading untrusted text) has no tools, no secrets; no other model sees untrusted text.
- [ ] No shell/code tool; no agent-held write tools; writes are deterministic code on schema-validated records.
- [ ] PII + injection neutralized in tests on the LLM-bound path (vault verbatim); fetcher egress policy on.
- [ ] `core.run()` callable by a future agent; no CLI-only logic.
- [ ] Idempotent writes; captions populated; skips logged.
