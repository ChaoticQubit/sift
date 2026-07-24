# share_capture setup — tap Share, get a note

The zero-ban-risk, real-time adapter (DESIGN §4): a tiny local endpoint receives shared
posts and typed notes, stashes them durably, and runs the normal pipeline. Replaces the
"DM myself" habit.

## 1. Run the endpoint

```bash
sift serve                          # loopback only (browser bookmarklet on this Mac)
SIFT_CAPTURE_TOKEN=<secret> sift serve --host 0.0.0.0   # LAN (phone) — token REQUIRED
```

Generate a token once: `openssl rand -hex 16` → put it in `.env` as `SIFT_CAPTURE_TOKEN`.
`sift serve` refuses a non-loopback bind without it. Keep it running with launchd
(`brew services`-style) or a login item; captures made while it is down are simply
made the next time it is up — nothing is lost mid-pipeline either way, shares land in
`<work_dir>/share_inbox/` first and survive crashes and offline model APIs.

The endpoint answers on `http://<mac-ip>:8595/capture`:

- `POST` JSON: `{"url": "...", "text": "...", "token": "..."}` (or token in an
  `X-Sift-Token` header) — the Shortcut path.
- `GET` `?url=...&text=...&token=...` — the bookmarklet path; responds "✓ Captured".

`url` and `text` are each optional but one is required; `text` rides as the note's
caption. Instagram permalinks dedup against `cookie_fetch`/`dyi_export` (same
canonical-permalink id), so sharing a post you later fetch still makes ONE note.

## 2. iPhone / iPad — Shortcuts share sheet

1. Shortcuts → **+** → name it **Sift**.
2. Shortcut settings → enable **Show in Share Sheet**; accept **URLs, Text, Safari web pages**.
3. Add action **Get Contents of URL**:
   - URL: `http://<mac-ip>:8595/capture`
   - Method **POST**, Request Body **JSON**:
     - `url` → Shortcut Input (as URL) — leave empty for pure text notes
     - `text` → Ask Each Time (your optional comment), or Shortcut Input for shared text
     - `token` → your `SIFT_CAPTURE_TOKEN`
4. In Instagram: any post → Share → **Sift**.

Same-Wi-Fi is required (the Mac is not on the public internet — by design). For
capture-anywhere, put the Mac and phone on Tailscale and use the Tailscale IP.

## 3. Android — HTTP Shortcuts app

Create a shortcut: POST `http://<mac-ip>:8595/capture`, JSON body as above, then
"Use in Share Sheet". (Or Tasker: HTTP Request action.)

## 4. Browser bookmarklet (this Mac)

```
javascript:location.href='http://127.0.0.1:8595/capture?token=TOKEN&url='+encodeURIComponent(location.href)
```

Replace `TOKEN` (optional on loopback). Click it on any page — including an open
Instagram post — and the page is captured.

## Notes

- The endpoint never fetches anything at capture time; link content is fetched later
  by the pipeline's SSRF-guarded extractor.
- `share_inbox/processed/` archives handled captures; `share_inbox/bad/` quarantines
  corrupt ones (both safe to prune).
