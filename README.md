# Sift

**Turn your Instagram saves into a searchable second brain.**

Sift ingests everything you've saved or DM'd to yourself on Instagram — reels, image carousels, videos, links, in any language — and turns it into a **local Obsidian markdown vault** you can actually search, with a **draft-then-approve digest** of anything time-sensitive (deadlines, applications) you might have missed.

It runs **entirely from Instagram's official data export** — no scraping, no logging into your account, no ban risk.

> Working name; rename freely. Personal / local-first tool. Not a product.

## What it does

1. **Ingest** — parse Meta's "Download Your Information" export (your Saved posts + self-DM thread).
2. **Fetch** — download public post media by permalink (`gallery-dl` / `yt-dlp`, no login).
3. **Extract** — OCR image carousels, speech-to-text reels/videos (via `freeflow`), pull readable content from links.
4. **Organize** — classify + summarize each item into an Obsidian-compatible markdown note.
5. **Search** — local RAG index; `sift search "what was that recipe"`.
6. **Review** — a digest of candidate deadlines/applications you confirm — nothing fires on its own.

## Why not just use \<X\>?

No existing tool does deep multimodal Instagram extraction **into your own local markdown**, with search **and** an action-review layer. The closest (Fabric) is cloud/closed and has no alerts. See `docs/DESIGN.md` §14.

## Status

Pre-build, Claude-Code-ready. Read **`docs/DESIGN.md`** (product doc / source of truth) and **`CLAUDE.md`** (dev contract), then start at **F0** in `docs/BUILD_CHECKLIST.md`.

## Quickstart (once built)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # set SIFT_VAULT_PATH, model provider/keys
# needs ffmpeg, gallery-dl, yt-dlp on PATH

sift run ~/Downloads/instagram-export.zip   # ingest + process
sift search "brown butter recipe"
```

## Design decisions (the short version)

- **Official export, not `instagrapi`.** The private-API path risks a permanent ban on your main account; the DYI export contains the same data at zero risk. See `docs/DESIGN.md` §4.
- **Fold-ready.** Built as one callable core module writing Life-Planner-compatible notes, so it can later become a background agent inside the "second brain that acts."
- **Draft-then-approve.** Action items are proposed, never auto-fired.

## Roadmap

`docs/BUILD_CHECKLIST.md` — F0 foundation → F1 ingest → F2 carousels+links → F3 reels STT → F4 RAG → F5 action review. Deferred: frame OCR, non-English translation, Data Portability API pull, Life Planner fold-in.
