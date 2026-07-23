# Sift

**Turn your Instagram saves into a searchable second brain.**

Sift ingests everything you've saved or DM'd to yourself on Instagram — reels, image carousels, videos, links, in any language — and turns it into a **local Obsidian markdown vault** you can actually search, with a **draft-then-approve digest** of anything time-sensitive (deadlines, applications) you might have missed.

> Working name; rename freely. Personal / local-first tool. Not a product.

## What it does

1. **Ingest** — three adapters (`docs/DESIGN.md` §4): parse Meta's official "Download Your Information" export (backfill + self-DMs, zero risk); a weekly read-only cookie-based fetch of your Saved folder (`gallery-dl`, your machine + home IP, low but non-zero risk); a share-sheet capture for new items (zero risk). No `instagrapi`, no mobile private API, ever.
2. **Extract** — the **caption first** (richest signal), then OCR image carousels, speech-to-text reels/videos, readable content from links. All deterministic code.
3. **Interpret** — one quarantined, tool-less Agno agent turns the untrusted text into a strict typed record (classification, summary, tags, action candidates). See `docs/AGENTS.md`.
4. **File** — each item becomes an Obsidian-compatible markdown note (verbatim caption preserved).
5. **Search** — local RAG index; `sift search "what was that recipe"`.
6. **Review** — a digest of candidate deadlines/applications you confirm — nothing fires on its own.

## Why not just use \<X\>?

No existing tool does deep multimodal Instagram extraction **into your own local markdown**, with search **and** an action-review layer. The closest (Fabric) is cloud/closed and has no alerts. See `docs/DESIGN.md` §14.

## Status

Pre-build. Read **`docs/DESIGN.md`** (product doc / source of truth) and **`CLAUDE.md`** (dev contract), then start at **F0** in `docs/BUILD_CHECKLIST.md`.

## Quickstart (once built)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # set SIFT_VAULT_PATH, model provider/keys
# needs ffmpeg, gallery-dl, yt-dlp on PATH

sift run --source dyi_export --path ~/Downloads/instagram-export.zip   # backfill
sift run --source cookie_fetch                                         # weekly sync
sift search "brown butter recipe"
```

## Design decisions (the short version)

- **Hybrid ingestion, never the private API.** The official export + a low-cadence read-only cookie fetch + a share-sheet. `instagrapi` is banned — permanent-ban risk on a main account. See `docs/DESIGN.md` §4.
- **Rule of Two, enforced structurally.** The only model that reads untrusted captions/links has no tools and no secrets; everything with capability is deterministic code on schema-validated records. See `docs/AGENTS.md` §2.
- **Fold-ready.** Built as one callable core module writing Life-Planner-compatible notes, so it can later become a background agent inside the "second brain that acts."
- **Draft-then-approve.** Action items are proposed, never auto-fired.

## Roadmap

`docs/BUILD_CHECKLIST.md` — F0 foundation → F0.5 guardrail harness → F1 export ingest → F2 cookie fetch → F3 extract+interpret → F4 reels STT → F5 RAG search → F6 action review → F7 share capture. Deferred: frame OCR, non-English translation, auto-linking, Data Portability API, Life Planner fold-in.
