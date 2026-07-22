# Sift — Build Checklist

Phased, checkbox tasks for Claude Code. Ship each phase before the next. ⭐ = spike-first. Read `DESIGN.md` + `AGENTS.md` for the why. `[S]/[M]/[L]` = size.

---

## F0 — Foundation `[M]`  *(no Instagram, no untrusted content yet)*
- [ ] `pyproject.toml` (deps incl. `agno`, `lancedb`, `presidio-analyzer`, `presidio-anonymizer`, `llm-guard`, `gallery-dl`, `yt-dlp`, `trafilatura`, `dateparser`, `faster-whisper`, `typer`; `[dev]`: pytest/ruff/black), `.gitignore`, `.env.example`.
- [ ] `config.py` — pydantic-settings: `SIFT_VAULT_PATH`, `SIFT_DB_URL` (sqlite), LLM provider+key, embedder, feature flags (`enable_instagrapi=false` and it stays unused).
- [ ] `models.py` — `Item` (incl. `caption`, `ingest_via`), `Extraction`, `ActionItem`, `Note`, `RunReport`.
- [ ] `db/storage.py` — Agno `Db` on SQLite (sessions + memory); dedup ledger.
- [ ] `vault/frontmatter.py` + `vault/writer.py` — §6 schema (incl. `caption`), idempotent write, classification routing.
- [ ] `agents/` — a minimal Agno agent wired to storage (hello-world) to prove the framework + version pin.
- [ ] `core.run(source, config) -> RunReport` skeleton; `cli.py` (typer) thin shells.
- [ ] `tests/fixtures/` — fake DYI export (saved_posts + self-thread + 1 image + 1 short mp4) with captions.
- [ ] ⭐ **Dry run:** a fixture item → correct note (caption populated), idempotent. *Done.*

## F0.5 — Guardrail harness `[M]`  dep: F0  *(do this before any untrusted content)*
- [ ] `guardrails/hooks.py` — base `pre_hooks`/`post_hooks` wiring.
- [ ] `guardrails/injection.py` — Agno `PromptInjectionGuardrail` + `UntrustedContentGuardrail` (spotlight/delimit, strip `U+E0000–U+E007F`, sanitize md links).
- [ ] `guardrails/pii.py` — Agno `PIIDetectionGuardrail(mask=True)` + `PresidioGuardrail(BaseGuardrail)`; output `SensitiveOutputGuardrail` (LLM Guard).
- [ ] `guardrails/egress.py` — outbound HTTP allowlist (instagram.com + own API).
- [ ] `agents/reader.py` (quarantined: no secrets, read tools only) + `agents/enricher.py` (privileged: sanitized data only, `requires_confirmation` on writes) — the **Rule of Two**.
- [ ] ⭐ **Guardrail tests:** a caption containing an injection payload + a fake SSN/email is neutralized (blocked/masked) and never reaches a privileged tool. *Done.* See `AGENTS.md`.

## F1 — Ingest: DYI export + captions `[M]`  dep: F0
- [ ] `ingest/dyi_export.py` — parse ZIP: saved posts, saved collections (names), self-DM/note-to-self thread, **captions**. JSON + HTML shapes. Stable `Item.id`.
- [ ] Wire into `core.run(source="dyi_export", path=...)`. *Done = your export backfills notes; captions populated; self-DMs included.*

## F2 — Ingest: cookie_fetch + captions `[L]`  dep: F1
- [ ] `ingest/cookie_fetch.py` — `gallery-dl --cookies-from-browser` on the `saved` extractor; caption via metadata postprocessor; `--download-archive` incremental; **read-only**; `sleep-request>=3s`; single session; **429 back-off (pause days)**. Playwright fallback stub.
- [ ] Weekly scheduler (cron/launchd) doc + `sift run --source cookie_fetch`. *Done = a scheduled run pulls only NEW saves, read-only, on your machine/home IP, backs off on 429.*

## F3 — Extract + enrich (through the agents) `[L]`  dep: F0.5, F1
- [ ] `extract/captions.py` (first-class), `extract/images_ocr.py` (loop N; PaddleOCR + VLM fallback), `extract/links.py` (trafilatura) — invoked by the **reader** agent as typed tools.
- [ ] `agents/enricher.py` — classify + summarize + candidate action items (strict Pydantic schema, reasoning on) on sanitized data → `Note`.
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
- [ ] Rule-of-Two intact; reader has no secrets/side-effecting tools.
- [ ] No shell/code tool; writes gated with `requires_confirmation`.
- [ ] PII + injection neutralized in tests; egress allowlist on.
- [ ] `core.run()` callable by a future agent; no CLI-only logic.
- [ ] Idempotent writes; captions populated; skips logged.
