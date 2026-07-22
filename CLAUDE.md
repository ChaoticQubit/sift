# CLAUDE.md — Sift Working Agreement

Guidance for Claude Code when developing this repo. Read `docs/DESIGN.md` (source of truth) and `docs/AGENTS.md` (agent + guardrail spec) first. This file is the short operational contract.

## What this is

**Sift** turns a user's Instagram saved posts + self-DMs into a searchable, local **Obsidian markdown second brain** with RAG search and a draft-then-approve **action-review** digest. **Personal, local-first.** Pipeline shape (decided 2026-07-22): **deterministic extract → sanitize → ONE quarantined tool-less Agno interpreter agent (untrusted text → strict typed record) → deterministic vault write.** Built as one callable `core.run()`, shaped to later fold into the "Life Planner" second-brain.

## Mandatory workflow — no skipping

Every code task runs through these gates in order.

### 0. Branch first — before touching anything

Every task starts on a fresh short-lived branch off `main` (`type/short-slug`: `feat/…`, `fix/…`, `docs/…`, `chore/…`). One branch = one logical task → PR → `main`. Never commit task work onto `main`. Confirm before any destructive git op.

### 1. Graph first — before reading source

When `graphify-out/graph.json` exists, answer codebase questions with `graphify query / path / explain` before opening files. Rebuild after code changes with `graphify update .` (structural-only, free). Never run a bare `/graphify .` semantic pass without an explicit decision.

### 2. Context-mode for anything you'll process

Don't pull raw bytes into context to filter/count/parse — use `ctx_batch_execute` / `ctx_execute` and surface only the result.

### 3. Grill before building

For any non-trivial build/change request: surface the open design questions and get them answered (grill-me) before writing code. Record decisions in `docs/DESIGN.md` §17.

### 4. Engineering practices

Trunk-based, small reviewable PRs, tests alongside code (software-practices). Offline tests only — see Conventions.

### 5. Laziest correct solution

Ponytail applies: stdlib before dependency, one line before fifty, no speculative abstraction. The guardrail harness and trust boundaries are explicitly exempt — never simplify those away.

## Hard rules (do not violate without an explicit decision in DESIGN.md / AGENTS.md)

1. **No `instagrapi`, no account login/scraping via the mobile private API — ever.** Ingestion is the three-adapter hybrid (`DESIGN.md` §4): `cookie_fetch` (weekly, read-only, `gallery-dl --cookies-from-browser`, user's machine + home IP), `share_capture` (share-sheet, zero-risk), `dyi_export` (official export ZIP → saved + self-DMs + captions). Any account-touching code is **read-only** (never like/follow/comment/DM), **weekly by default (daily ceiling, never hourly)**, one persistent session, back off on 429.
2. **Captions are first-class.** Extract and analyze `caption` for **every** item (from gallery-dl `description` / share JSON / export). It's often the richest field — never drop it.
3. **All agents are Agno v2.** Pin the version; ignore pre-2026 tutorials (v1→v2 was a breaking rewrite). Storage = Agno `Db` on SQLite; RAG = Agno Knowledge on LanceDB; reasoning via `ReasoningTools` + Claude. Not LangChain.
4. **The Rule of Two is structural (`docs/AGENTS.md` §2):**
   - The **interpreter** is the ONLY model that reads untrusted content (captions, OCR, transcripts, link text). It has **NO tools and NO secrets**; output is a strict Pydantic `Interpretation` record; off-schema output is rejected and logged.
   - Extraction is deterministic pipeline code (`extract/router.py`) — never an agent tool. Vault writes, tagging, dedup, digest are plain code on schema-validated records. **No agent holds write tools in v1.**
   - **No generic shell/code/`run_command` tool anywhere. No code execution** by default; if ever needed, gVisor/Firecracker sandbox.
   - **PII masking applies to LLM-bound text only** (Agno `PIIDetectionGuardrail` + Presidio in; LLM Guard `Sensitive` out). The vault note keeps the verbatim caption from the `Item`.
   - **Untrusted text hardening:** spotlight/delimit as DATA, strip Unicode tag chars `U+E0000–U+E007F`, sanitize markdown links.
   - **Egress by trust:** interpreter has none (no tools); gallery-dl/yt-dlp scoped to instagram.com + its CDNs (cookies allowed); link fetcher credential-less, any public URL, private/loopback IPs blocked.
5. **Write Life-Planner-compatible frontmatter** (schema `DESIGN.md` §6, incl. `caption`, `ingest_via`) into the user's real vault. Idempotent, dedupe by stable `Item.id`.
6. **No core logic in `cli.py`.** `core.run(source, config)` is the public entrypoint a future non-interactive Life-Planner agent imports. No `input()`/terminal assumptions in `core`/`agents`/`ingest`/`extract`/`vault`/`rag`/`review`.
7. **Draft-then-approve.** No autonomous alerts/writes in v1; action items → `Action Review.md` with `status: pending`.
8. **Local-first + provider-swappable.** Vault path, model provider, keys, DB via config/env. Support local models where practical. State lives in `<vault>/.sift/` by default (`SIFT_WORK_DIR` overrides).
9. **Respect the deferred list:** no frame OCR, no non-English target translation, no auto-linking, no unattended alerting, no GUI, no Meta Data Portability API in v1.

## Repo layout

```
sift/
  docs/DESIGN.md · docs/AGENTS.md · docs/BUILD_CHECKLIST.md
  CLAUDE.md · README.md · pyproject.toml · .env.example
  src/sift/
    core.py                 # run(source, config) -> RunReport  (public entrypoint)
    config.py · models.py · cli.py
    ingest/                 # cookie_fetch.py, share_capture.py, dyi_export.py  (NO instagrapi)
    agents/                 # interpreter.py (quarantined, tool-less), search.py
    extract/                # router.py + captions.py, images_ocr.py, reels_stt.py, links.py  (deterministic; frames_ocr deferred)
    guardrails/             # hooks.py (pre/post), pii.py (Presidio), injection.py, egress.py (fetcher policy)
    vault/                  # frontmatter.py, writer.py
    rag/                    # knowledge.py (Agno+LanceDB), search.py
    review/                 # action_items.py, digest.py
    db/                     # storage.py (Agno Db / SQLite), dedup ledger
  tests/ (offline fixtures only)
```

## Setup & commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                  # core; add extras per phase:
pip install -e ".[guardrails]"           # F0.5+ (presidio, llm-guard — heavy)
pip install -e ".[stt]"                  # F4 (faster-whisper)
pip install -e ".[rag]"                  # F5 (lancedb)
cp .env.example .env   # set SIFT_VAULT_PATH, model + embedder keys
# system deps: ffmpeg, gallery-dl, yt-dlp on PATH
sift run --source dyi_export --path ~/Downloads/instagram-export.zip   # backfill
sift run --source cookie_fetch                                         # weekly saved-folder sync
sift search "brown butter recipe"
```

## Conventions

- Python 3.11+, type hints, `pydantic` schemas, `ruff`+`black`, `pytest`.
- Logic in plain functions; the interpreter agent thin; side effects behind mockable adapters.
- Uniform contracts: `Item` (with `caption`), `Extraction`, `Interpretation`, `Note`. These are the fold-in seam — keep stable.
- Tests run **offline** (fixtures only): no Instagram, no paid API, no live model. Mock the agent/model.
- **Guardrail tests are mandatory:** an injection string in a caption and a fake PII value must be neutralized in tests before F3 ships.
- Log what you skip (unfetchable media, low-confidence transcripts, guardrail blocks) — never silently drop.

## Current status

Docs (DESIGN v3, AGENTS, BUILD_CHECKLIST) + skeleton exist; logic stubbed. **Next: F0** then **F0.5 (guardrail harness)** in `docs/BUILD_CHECKLIST.md`.
