# CLAUDE.md — Sift

Guidance for Claude Code when developing this repo. Read `docs/DESIGN.md` (source of truth) and `docs/AGENTS.md` (agent + guardrail spec) first. This file is the short operational contract.

## What this is

**Sift** turns a user's Instagram saved posts + self-DMs into a searchable, local **Obsidian markdown second brain** with RAG search and a draft-then-approve **action-review** digest. **Personal, local-first.** Agents are **Agno v2** with a **guardrail harness**. Built as one callable `core.run()`, shaped to later fold into the "Life Planner" second-brain.

## Hard rules (do not violate without an explicit decision in DESIGN.md / AGENTS.md)

1. **No `instagrapi`, no account login/scraping via the mobile private API — ever.** Ingestion is the three-adapter hybrid (`DESIGN.md` §4): `cookie_fetch` (weekly, read-only, `gallery-dl --cookies-from-browser`, user's machine + home IP), `share_capture` (share-sheet, zero-risk), `dyi_export` (official export ZIP → saved + self-DMs + captions). Any account-touching code is **read-only** (never like/follow/comment/DM), **weekly by default (daily ceiling, never hourly)**, one persistent session, back off on 429.
2. **Captions are first-class.** Extract and analyze `caption` for **every** item (from gallery-dl `description` / share JSON / export). It's often the richest field — never drop it.
3. **All agents are Agno v2.** Pin the version; ignore pre-2026 tutorials (v1→v2 was a breaking rewrite). Storage = Agno `Db` on SQLite; RAG = Agno Knowledge on LanceDB; reasoning via `ReasoningTools` + Claude. Not LangChain.
4. **Guardrail harness on every agent (`docs/AGENTS.md`):**
   - **Rule of Two:** a QUARANTINED `reader` agent (touches raw captions/links; no secrets, no side-effecting tools; emits typed records only) and a PRIVILEGED `enricher` agent (acts only on sanitized structured data).
   - **PII:** `PIIDetectionGuardrail(mask=True)` + Presidio (custom `BaseGuardrail`) on input; LLM Guard `Sensitive` on output; egress allowlist (instagram.com + own API only).
   - **No generic shell/code/`run_command` tool.** Only narrow typed `@tool` functions. Gate every write tool with `requires_confirmation`.
   - **No code execution** by default; if ever needed, gVisor/Firecracker sandbox (no network, read-only FS).
   - **Untrusted text:** spotlight/delimit captions as DATA, strip Unicode tag chars `U+E0000–U+E007F`, sanitize markdown links.
5. **Write Life-Planner-compatible frontmatter** (schema `DESIGN.md` §6, incl. `caption`, `ingest_via`) into the user's real vault. Idempotent, dedupe by stable `Item.id`.
6. **No core logic in `cli.py`.** `core.run(source, config)` is the public entrypoint a future non-interactive Life-Planner agent imports. No `input()`/terminal assumptions in `core`/`agents`/`tools`/`ingest`/`vault`/`rag`/`review`.
7. **Draft-then-approve.** No autonomous alerts/writes in v1; action items → `Action Review.md` with `status: pending`.
8. **Local-first + provider-swappable.** Vault path, model provider, keys, DB via config/env. Support local models where practical.
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
    agents/                 # reader.py (quarantined), enricher.py (privileged), search.py, team.py
    tools/                  # ocr_images.py, transcribe_audio.py, fetch_link.py, save_item.py, tag_item.py  (typed @tool; no shell)
    guardrails/             # hooks.py (pre/post), pii.py (Presidio), injection.py, egress.py
    extract/                # captions.py, images_ocr.py, reels_stt.py, links.py  (frames_ocr.py deferred)
    vault/                  # frontmatter.py, writer.py
    rag/                    # knowledge.py (Agno+LanceDB), search.py
    review/                 # action_items.py, digest.py
    db/                     # storage.py (Agno Db / SQLite), dedup ledger
  tests/ (offline fixtures only)
```

## Setup & commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # set SIFT_VAULT_PATH, model + embedder keys, SIFT_DB_URL
# system deps: ffmpeg, gallery-dl, yt-dlp on PATH
sift run --source dyi_export ~/Downloads/instagram-export.zip   # backfill
sift run --source cookie_fetch                                  # weekly saved-folder sync
sift search "brown butter recipe"
```

## Conventions

- Python 3.11+, type hints, `pydantic` schemas, `ruff`+`black`, `pytest`.
- Agents thin; logic in tools/functions; side effects behind mockable adapters.
- Uniform contracts: `Item` (with `caption`), `Extraction`, `Note`. These are the fold-in seam — keep stable.
- Tests run **offline** (fixtures only): no Instagram, no paid API, no live model. Mock the agent/model.
- **Guardrail tests are mandatory:** an injection string in a caption and a fake PII value must be neutralized in tests before F3 ships.
- Log what you skip (unfetchable media, low-confidence transcripts, guardrail blocks) — never silently drop.

## Current status

Docs (DESIGN, AGENTS, BUILD_CHECKLIST) + skeleton exist; logic stubbed. **Next: F0** then **F0.5 (guardrail harness)** in `docs/BUILD_CHECKLIST.md`.
