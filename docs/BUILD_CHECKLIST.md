# Sift — Build Checklist

Phased, checkbox tasks for Claude Code. Ship each phase before the next. ⭐ = spike-first. Read `DESIGN.md` + `AGENTS.md` for the why. `[S]/[M]/[L]` = size.

---

## F0 — Foundation `[M]`  *(no Instagram, no untrusted content yet)*
- [ ] `pyproject.toml` — core deps light (`agno`, `gallery-dl`, `yt-dlp`, `trafilatura`, `typer`, `pydantic*`, `python-frontmatter`); heavy deps in extras: `[stt]` (faster-whisper, F4), `[rag]` (lancedb, F5); `[dev]`: pytest/ruff/black. `.gitignore`, `.env.example`.
- [ ] `config.py` — pydantic-settings: `SIFT_VAULT_PATH`, `SIFT_WORK_DIR` (default `<vault>/.sift`), LLM provider+key, embedder. No instagrapi flag, no flags for unbuilt features — the ban is a rule, absence is the flag (CLAUDE.md 1).
- [ ] `models.py` — `Item` (incl. `caption`, `ingest_via`), `Extraction`, `Interpretation` (classification = closed enum; ActionItem status literal, confidence bounded), `ActionItem`, `Note`, `RunReport`.
- [ ] `db/storage.py` — SQLite **dedup ledger only** (the interpreter is stateless; Agno `Db` sessions/memory arrive with the first conversational agent — AGENTS.md §1).
- [ ] `vault/frontmatter.py` + `vault/writer.py` — §6 schema (incl. `caption`), idempotent write, classification routing, caption/extracted bodies **fenced** (render-defanged), model-produced fields escaped.
- [ ] `agents/` — a minimal Agno agent wired to storage (hello-world) to prove the framework + version pin.
- [ ] `core.run(source, config) -> RunReport` skeleton; `cli.py` (typer) thin shells.
- [ ] `tests/fixtures/` — fake DYI export (saved_posts + self-thread + 1 image + 1 short mp4) with captions.
- [ ] ⭐ **Dry run:** a fixture item → correct note (caption populated), idempotent. *Done.*

## F0.5 — Guardrail harness `[M]`  dep: F0  *(before any untrusted text reaches a model — F3 depends on this)*
- [ ] `guardrails/injection.py` — `UntrustedContentGuardrail` (spotlight/delimit, strip `U+E0000–U+E007F`, sanitize md links; deterministic sanitize usable outside hooks too) + `InjectionFlagGuardrail` (**log-only** pattern annotation — never blocks; AGENTS.md §3).
- [ ] `guardrails/pii.py` — Agno `PIIDetectionGuardrail(mask=True)` config + the same masking exposed as a plain callable for the F5 embed path. Vault stays verbatim (AGENTS.md §3a).
- [ ] `guardrails/egress.py` — SSRF-safe fetch policy: credential-less session; resolve-once + pin-IP; redirects re-validated per hop; IPv4 private/loopback/link-local + `0.0.0.0` + IPv6 (`::1`, `fc00::/7`, `fe80::/10`, mapped forms) + encoded-IP forms blocked (AGENTS.md §3a).
- [ ] `agents/interpreter.py` — the quarantined interpreter skeleton: strict `Interpretation` output schema, pre_hooks wired inline, NO tools, NO secrets, NO session memory (stateless per item) — the **Rule of Two** (AGENTS.md §2).
- [ ] Boundary value re-forcing in pipeline code: `status: pending` re-initialized on every interpreter-emitted action item; confidence clamped; classification enum-checked.
- [ ] ⭐ **Guardrail tests:** a caption containing an injection payload + a fake SSN/email is masked on the model-bound path (vault copy verbatim), off-schema interpreter output is rejected, and a schema-valid record claiming `status: confirmed` comes out `pending`. *Done.* See `AGENTS.md`.

## F1 — Ingest: DYI export + captions `[M]`  dep: F0
- [ ] `ingest/dyi_export.py` — parse ZIP: saved posts, saved collections (names), self-DM/note-to-self thread, **captions**. JSON shape; an HTML-format export is refused loudly with re-request-as-JSON guidance (DESIGN §17). Stable `Item.id`.
- [ ] Wire into `core.run(source="dyi_export", path=...)`. *Done = your export backfills notes; captions populated; self-DMs included.*

## F2 — Ingest: cookie_fetch + captions `[L]`  dep: F1
- [ ] `ingest/cookie_fetch.py` — `gallery-dl --cookies-from-browser` on the `saved` extractor; caption via metadata postprocessor; `--download-archive` incremental; **read-only**; `sleep-request>=3s`; single session; **429 back-off (pause days)**. (No pre-built fallback — add one the week the extractor actually breaks.)
- [ ] **Cadence enforced in code:** last-run timestamp check refuses a fetch < 20h since the previous one (the real "weekly default / daily ceiling / never hourly" guard — a config string enforces nothing).
- [ ] **Scope enforced in code:** fetched-URL hosts checked against instagram.com + CDN allowlist; yt-dlp invoked only on instagram.com permalinks (AGENTS.md §3a).
- [ ] Weekly scheduler (cron/launchd) doc + `sift run --source cookie_fetch`. *Done = a scheduled run pulls only NEW saves, read-only, on your machine/home IP, backs off on 429.*

## F3 — Extract + interpret `[L]`  dep: F0.5, F1
- [ ] `extract/captions.py` (first-class), `extract/images_ocr.py` (loop N; **vision-LLM only** — tool-less call, output re-enters the sanitize path; DESIGN §7), `extract/links.py` (trafilatura via the egress-guarded session) — invoked by `extract/router.py` as **deterministic pipeline code**, not agent tools.
- [ ] `agents/interpreter.py` — full implementation: sanitized text → classify + summarize + tags + candidate action items (strict `Interpretation` schema; reasoning off by default); deterministic writer consumes the validated, value-re-forced record → `Note`.
- [ ] *Done = a real carousel and a real link post become good notes; caption analyzed.*

## F4 — Reels / video STT `[M]`  dep: F3
- [ ] `extract/reels_stt.py` — ffmpeg → freeflow/faster-whisper; language detect + confidence; flag low-confidence; no translation beyond X→English (off by default). *Done.*

## F5 — RAG search `[M]`  dep: F3
- [ ] `rag/knowledge.py` — Agno Knowledge over LanceDB (hybrid); incremental; **embed-bound text masked** with the shared PII callable. `rag/search.py` — **deterministic hybrid query, no agent, no model call** (DESIGN §10) + `sift search`. *Done = find a note by content.*

## F6 — Action review `[M]`  dep: F3
- [ ] `review/action_items.py` (dues arrive as typed dates from the schema — no raw-text date parsing needed) + `review/digest.py` (`Action Review.md`, grouped, links back, `status: pending`; confirm/dismiss updates frontmatter). *Done = a real deadline surfaces; nothing fires unconfirmed.*

## F7 — share_capture `[M]`  dep: F0.5
- [ ] `ingest/share_capture.py` + a small capture endpoint; iOS/Android Share-Sheet/Shortcut + browser action → writes shared posts + notes into Sift. *Done = tapping Share files a note in real time; replaces self-DM.*

## F8 — dm_fetch: proactive self-DM sync `[M]`  dep: F2, F0.5
- [x] `ingest/dm_fetch.py` — read-only Playwright open of the **note-to-self thread only** on a dedicated Sift profile (headed, parked off-screen); new posts' permalinks (+ any typed note) → `Item(ingest_via=dm_fetch)`; full caption/media fill in via the existing media-defer → gallery-dl path (dm_fetch never opens a post). Reuses cookie_fetch's cadence/attempt/back-off/lock rig (`who=` param); a login/checkpoint challenge stops the run and grows back-off, **never auto-solved**. `sift dm-login` seeds / re-auths the profile. Wired into `core.run(source="dm_fetch")`. Offline tests mock the Playwright seam (`test_dm_fetch.py`) incl. the mandatory injection+PII guardrail case.
- [x] Review hardening (DeepSec + `/code-review`, 2026-08-19): **one account-wide lock** (`instagram-account.lock`) shared by cookie_fetch.pull + dm_fetch.pull + dm_fetch.login (no two concurrent sessions, no login-vs-run profile corruption); browser profile moved **out of the (sync-prone) vault** to an OS state dir + chmod 0700 (`SIFT_DM_PROFILE_DIR`); login state re-asserted **after** the harvest so a mid-run checkpoint trips the back-off instead of stamping success; `context.close()` isolated so it cannot mask a challenge; watermark boundary inclusive + finite/future-guarded; `saved_at=None` on unknown ts; exact-match self-thread (fail-closed).
- [x] Live thread-open + permalink harvest, tuned against a real logged-in session (2026-08-19, 95 posts): `_open_self_thread` navigates straight to the **direct thread URL** (`SIFT_DM_THREAD_URL`) and verifies the landing (challenge → back-off; any other redirect off `/direct/t/` → fail closed), and shared-post permalinks are read from the thread's own **GraphQL responses** (`target_url`), never the DOM — Instagram renders each share as a click-walled card with no permalink anchor, so the DOM-anchor approach could not work. A bounded upward scroll pages older history in and stops when dry; the harvest never clicks a card.
- [ ] Deferred — both degrade cleanly today, so neither blocks the scheduled run: (1) per-message **timestamp** — currently left 0.0, so the **dedup ledger** (not a watermark) owns new-only idempotency and `saved_at` is left empty, matching dyi_export; (2) typed-note **text** shared alongside a post — currently empty (the permalink alone yields a full note via media enrichment). Plus the weekly launchd/cron scheduling doc. *Done = a scheduled run pulls only NEW self-DM'd posts, read-only, home IP, backs off on a challenge.*
- [ ] Backlog (pre-existing, surfaced by review): note **filename** identity is a 32-bit truncated sha256 (`core.py:77` + `vault/writer.py`) — a first-8-hex collision can overwrite an unrelated note. Repo-wide + needs a filename migration; own ticket, not dm_fetch.

---

## v-next (DEFERRED)
- [ ] `extract/frames_ocr.py` (keyframe dedup + OCR). [ ] Non-English target translation. [ ] Auto-linking. [ ] Meta Data Portability API adapter (only if Sift incorporates / US opens). [ ] Fold into Life Planner (scheduled Agno agent + dashboard + confirmed items → planner).

## Guardrails (check every phase)
- [ ] No `instagrapi`; account code is read-only, weekly, one session, 429 back-off.
- [ ] Rule-of-Two intact: every model touching untrusted content (interpreter; F3 vision-OCR) is tool-less, secret-less, stateless; no other model sees untrusted content.
- [ ] No shell/code tool; no agent-held tools at all; writes are deterministic code on schema-validated records with security-relevant values re-forced.
- [ ] PII masked in tests on every model-API-bound path (interpreter + embedder; vault verbatim); sanitize applied; fetcher egress policy on.
- [ ] `core.run()` callable by a future agent; no CLI-only logic.
- [ ] Idempotent writes; captions populated; skips logged.
