# Sift — Design Doc / Product Documentation

*Working name: **Sift** — "turn your Instagram saves into a searchable second brain." Rename freely.*
*Created 2026-07-22 · Updated 2026-07-22 (v3: quarantined-interpreter pipeline — deterministic extract/write, one tool-less LLM on untrusted text) · Owner: Spongebob*
*Single source of truth. Living document — edit as decisions change. Agent/guardrail detail lives in `AGENTS.md`.*

---

## 0. TL;DR

You save a lot to Instagram — reels, carousels, links, videos of people talking, content in several languages — by sending it to yourself in DMs or hitting *Save*. It piles up in a folder you never revisit, you can't search *inside* it, and you don't know if something in there needs action (an application, a deadline). For many saves the reel is fluff and the **caption** is the real content.

**Sift** is a **local, personal** system that:

1. **Ingests** everything you saved / DM'd-to-yourself via a **local-first hybrid** (weekly cookie-based fetch of your Saved folder + a share-sheet capture path + an occasional official export) — see §4. No cloud scraping of your account.
2. **Extracts** the content by type — **the caption first** (structured text, richest signal), then OCR for image carousels, speech-to-text for reels/videos (via `freeflow`), readable-content for links.
3. **Files** each item as an **Obsidian-compatible markdown note** in your vault.
4. **Indexes** it for **RAG search** ("what was that recipe with brown butter?").
5. **Surfaces** a **review digest** of candidate time-sensitive items (deadlines, applications) that **you confirm** — no unattended autopilot.

It is built as a **deterministic pipeline around one quarantined Agno interpreter agent** behind one callable core, with a **guardrail harness** (PII on LLM-bound text, no tools for any model that reads untrusted content — the Rule of Two). It is deliberately shaped to **fold into the Life Planner "second brain that acts"** later as a background agent + dashboard.

---

## 1. Why this exists (personal-first)

- **The pain is real and verified.** "My saved folder is a graveyard" is a widely-documented complaint. Instagram's native *Saved* gives you no text search inside posts, no transcription, no OCR, no "did I miss anything" surface.
- **No existing tool does your specific combo.** Every competitor covers a slice and writes to *their* cloud. None do deep multimodal extraction (caption + carousel OCR + multilingual reel STT + link content) **into your own local markdown vault** with RAG **and** an action-review layer. (Fabric — the closest — was rejected 2026-07-22: cloud/closed, fails local-first, no action alerts.)
- **You're equipped.** You built `freeflow` (STT); in 2026 the ML blocks are commoditized. The hard part is ingestion transport, extraction precision, and agent safety — not models (§13).

**This is a personal tool, not a product.** As a paid product it's Bad (saturated; read-later graveyard; Instagram absorbing the job; commoditized wedges). Build it for yourself; open-source it if you want.

---

## 2. North Star & Principles (hard rules)

**North star:** everything meaningful you ever saved on Instagram becomes *findable, readable, and actionable* from your own vault — with near-zero ongoing effort.

1. **Local-first.** Data lives in your Obsidian vault as plain markdown; agent state in a local DB. You own it. Models are provider-swappable and can run local.
2. **Minimize account risk; never the mobile private API.** Ingestion is the **local-first hybrid** in §4. **`instagrapi` (mobile private API) is banned as a dependency** — it's the highest-ban-risk path and it's your main account. The recurring path is a *read-only, low-cadence, cookie-authenticated* local fetch; new items come in via a zero-risk share-sheet; DMs/backfill come from the official export.
3. **Captions are first-class.** Every item's caption is extracted and analyzed, not treated as metadata. It's often the richest content.
4. **Agno for all agents.** Every agent in Sift (and across the broader project) is built on **Agno v2** — persistent storage, RAG, reasoning, tools, MCP. Not LangChain. See `AGENTS.md`.
5. **Guardrail harness on every agent.** The **Rule of Two**, enforced structurally: any model that touches untrusted content (captions, images, transcripts, link text) is **tool-less and secret-less**; everything with capability (extraction dispatch, vault writes, tagging) is deterministic code acting on schema-validated typed records whose security-relevant values are re-forced, not trusted. PII is masked on everything **model-API-bound** (interpreter input and embedder input alike) — the vault keeps your data verbatim. **No generic shell/code tool anywhere**; sandbox any unavoidable code-exec. See `AGENTS.md`.
6. **Draft-then-approve.** Action items are *proposed* into a review digest; nothing schedules/alerts autonomously in v1.
7. **Fold-ready.** Same vault + Life-Planner-compatible frontmatter; one callable `core.run()`; ingestion behind swappable adapters so a future scheduled agent just changes the source.
8. **Thin v1, honest scope.** Ship reliable modalities first; defer the time-sinks (§3).

**Non-goals for v1:** unattended/autopilot alerting; non-English *target* translation; burned-in-caption video-frame OCR; a GUI (arrives via Life Planner); the Meta Data Portability developer API (§4); `instagrapi`; multi-user/product/billing.

---

## 3. Scope

| Stage | v1 does | Deferred (v-next) |
|------|---------|-------------------|
| Ingest — saved posts | **Weekly local cookie-fetch** (gallery-dl) of your Saved folder, incremental | — |
| Ingest — new items | **Share-sheet / shortcut** capture-at-save-time | — |
| Ingest — backfill + DMs | **Official DYI export** parsed locally (saved + self-DM thread) | Data Portability API (registry-gated; §4) |
| **Captions** | **Extract + analyze caption for every post** (first-class) | — |
| Images / carousels | OCR every image in a carousel (loop all N) | Layout-aware structured OCR |
| Reels / videos | STT (major languages) via freeflow / faster-whisper | Non-English target translation; diarization |
| On-screen video text | — | Burned-in caption / graphic frame OCR |
| Links | Readable content extraction | Forum/product special-casing |
| Organize | Agno agent: classify + summarize → markdown note | Auto-linking |
| Search | RAG index (Agno knowledge) + query | Chat UI |
| Action review | Candidate deadlines/applications → digest you confirm | Push/calendar (via Life Planner) |
| Agents | Agno agents + guardrail harness (Rule of Two) | more agents authored via the dashboard |

---

## 4. Ingestion & automation (the transport decision)

**The constraint (verified 2026):** there is **no official, automated, push-button way** for a US solo dev to get saved posts + self-DMs on a schedule.
- **Meta Data Portability developer API** *can* do recurring saved-post transfers, but requires DTI Data Trust Registry vetting (an **incorporated entity**, privacy policy, and a **paid third-party security audit** for sensitive data), a Meta Business Manager, and a ~30-day Meta review — and it **does not expose DMs/Messages as a data type at all** (only Photos/Videos/Media/Social-Posts/Notes/Archives). It's also DMA/EU-centric with uncertain US availability. **Deferred** — revisit only if Sift incorporates or US portability opens.
- **Native recurring export** ("Transfer Your Information" on a schedule) is **photos/videos only** — excludes saved posts and messages. Not usable as the backbone.

**The architecture: a local-first hybrid — three ingest adapters feeding one normalized `Item` pipeline.**

| Adapter | Handles | Automation | Ban risk | How |
|--------|---------|-----------|----------|-----|
| `cookie_fetch` | **Saved posts** (recurring) | Full (weekly cron/launchd) | **Low, non-zero** | `gallery-dl --cookies-from-browser` on the Instagram `saved` extractor, run on **your machine + home IP**; captions via metadata postprocessor; `--download-archive` for incremental. Read-only. Playwright fallback if web GraphQL shifts. |
| `share_capture` | **New items** going forward | Full, real-time | **Zero** | A Share-Sheet/Shortcut (iOS/Android) + browser action that writes shared posts + typed notes straight into Sift's capture endpoint — replaces the "DM myself" habit. |
| `dyi_export` | **Backfill + full self-DM history** | Semi (occasional, reminder-driven) | **Zero** | Parse the official "Download Your Information" ZIP locally — it contains saved posts, the self-DM thread, and captions. Seeds history; the only safe route to already-sent DMs. |
| `dm_fetch` | **New self-DM'd posts** (recurring) | Full (weekly cron/launchd) | **Low, non-zero** | Read-only Playwright open of your **note-to-self thread only**, by its **stable direct thread URL** (`SIFT_DM_THREAD_URL` — the inbox list is virtualized + click-walled, so the thread id is the reliable handle), on a **dedicated Sift browser profile** logged in once by hand, on **your machine + home IP**, headed but parked off-screen. Harvests shared-post permalinks read from the thread's own **GraphQL responses** (the DM DOM renders each share as a click-walled card with no permalink anchor — the permalink lives only in the network payload's `target_url`); full caption/media fill in via the same media-defer → gallery-dl path (dm_fetch never opens a post, never clicks a card). The one recurring authenticated DM read (decided 2026-08-19); a login/checkpoint challenge → stop + back-off, **never auto-solved**. Shares **one account-wide lock** with `cookie_fetch` (never two concurrent sessions, incl. `sift dm-login`); the profile (a live session) lives **outside the vault** by default (`SIFT_DM_PROFILE_DIR`), never in a cloud-synced folder. |

**Safety rules baked into `cookie_fetch` (and any account-touching code):**
- **Read-only only.** Never like/follow/comment/DM — write actions trigger most bans.
- **Weekly cadence by default; daily is the ceiling; hourly is forbidden.** Saved lists append a few items/week — low cadence is the single biggest ban-risk lever and buys back nothing.
- **Incremental diffing** so each run is a handful of requests; `sleep-request ≥ 3s`; **one persistent session** (never automated fresh logins); **exponential back-off (pause days) on the first HTTP 429**.
- Runs on the user's residential IP, never a cloud/datacenter IP.

**Honest statement:** `cookie_fetch` is still unofficial (ToS) and carries *some* risk on the main account. It is materially lower-risk than `instagrapi` (real web session, home IP, read-only, low cadence, few requests), and the zero-risk adapters (`share_capture`, `dyi_export`) carry the DMs and new items — so the strategic direction is to lean on `share_capture` over time and let `cookie_fetch` be the backfill-the-old-saves workhorse. `dm_fetch` (decided 2026-08-19) adds a *proactive, no-habit-change* recurring path for new self-DM'd posts, at the same low-but-non-zero profile as `cookie_fetch` (read-only, home IP, low cadence, single session, hard back-off, challenge → stop). It uses a real browser on a dedicated profile (most human fingerprint) rather than a scraper client, and touches **only** the self-thread; `dyi_export` remains the only route to already-sent DM history. Randomised multi-transport / anti-detection evasion was explicitly rejected (2026-08-19): at this volume the discriminating signal is fingerprint + endpoint, not timing, and rotation raises surface — consistency + minimal requests + fast retreat is the actual risk lever.

---

## 5. Architecture

**v1 = a deterministic pipeline around one quarantined Agno interpreter agent, behind a callable `core.run()`, invoked by a thin CLI now; a scheduled agent + dashboard later (Life Planner).** Full agent/guardrail spec in `AGENTS.md`; summary here.

The load-bearing shape (the **Rule of Two**, enforced structurally): **every model that touches untrusted content is tool-less and secret-less; everything with capability is plain code operating on schema-validated typed records.** Exactly two such model touchpoints exist: the **interpreter** (text → record) and, from F3, the **vision-OCR call** (image → text, whose output re-enters the same sanitize path). Extraction is otherwise deterministic (`router.py` dispatches by medium), writes need no model, no agent holds tools, and `sift search` is a deterministic index query — no model reads vault content back (decided 2026-07-22).

```mermaid
flowchart TD
    subgraph Ingest [ingest adapters -> normalized Item]
      A1[cookie_fetch<br/>weekly, saved folder] 
      A2[share_capture<br/>real-time, new items]
      A3[dyi_export<br/>backfill + self-DMs]
      A4[dm_fetch<br/>weekly, self-DM thread]
    end
    A1 --> N[Item + caption]
    A2 --> N
    A3 --> N
    N --> X[deterministic extract<br/>router -> caption / OCR / STT / link<br/>no LLM, no agent]
    X --> SZ[sanitize<br/>spotlight · strip U+E0000–E007F<br/>PII-mask the LLM-bound copy]
    SZ --> IA[QUARANTINED interpreter agent<br/>Agno · NO tools · NO secrets<br/>untrusted text -> strict typed record]
    IA --> VAL{{schema validation}}
    VAL --> W[deterministic writer<br/>markdown + frontmatter<br/>verbatim caption from Item]
    W --> V[(Obsidian vault)]
    V --> K[Agno Knowledge / RAG<br/>LanceDB]
    VAL --> DR[Action Review.md<br/>you confirm]
    K --> S[sift search]
    style V fill:#2d3b2d,color:#fff
    style DR fill:#3b2d2d,color:#fff
    style IA fill:#332a3d,color:#fff
```

**Components (`src/sift/`):**
- `ingest/` — `cookie_fetch.py`, `share_capture.py`, `dyi_export.py`; each emits normalized `Item`s (with `caption`). No `instagrapi`.
- `agents/` — one Agno agent: the **interpreter** (quarantined, tool-less, **stateless per item**: untrusted text → strict `Interpretation` record). No reader/enricher pair, no Team, no search agent: everything else is deterministic code.
- `extract/` — extractors called by `router.py` (plain code, not agent tools; the F3 vision-OCR call is the one model inside, tool-less). **No shell/code tool anywhere.**
- `guardrails/` — deterministic sanitize (spotlight, Unicode-tag strip, md neutralize), built-in PII masking for everything model-API-bound (interpreter + embedder), log-only injection-pattern flagging, and the fetcher egress policy. See `AGENTS.md`.
- `vault/` — `frontmatter.py` (schema §6) + `writer.py` (idempotent).
- `rag/` — Agno Knowledge over LanceDB (+ `search.py`).
- `review/` — `action_items.py` + `digest.py`.
- `core.py` — `run(source, config) -> RunReport`: the one entrypoint a future Life-Planner agent imports.
- `db/` — Agno storage (SQLite): agent sessions, user memory, dedup ledger.
- `config.py` / `cli.py` — env config + thin typer shell.

---

## 6. Data model (vault)

**Layout:** `<vault>/Captures/`, `<vault>/media/`, `<vault>/Action Review.md`, `<vault>/.sift/` (SQLite + LanceDB + dedup ledger, git-ignored).

**Note frontmatter (extends Life Planner's capture schema; `caption` promoted to a top field):**

```yaml
type: capture
source: https://www.instagram.com/reel/ABC123/
platform: instagram
medium: reel            # reel | carousel | image | video | link | text
ingest_via: cookie_fetch     # cookie_fetch | share_capture | dyi_export
caption: |                   # FIRST-CLASS: the post's caption text, verbatim
  Brown butter chocolate chip cookies 🍪 recipe below...
classification: educational   # educational | reference | entertainment
saved_collection: Recipes
saved_at: 2026-03-12
created: 2026-07-22
languages: [en]
has_action_items: true
action_items:
  - text: "Apply to the X fellowship"
    due: 2026-08-01
    confidence: 0.7
    status: pending
links: []
media_files: [media/reel_ABC123.mp4]
source_export: instagram-2026-07-20
tags: [recipe, dessert]
```

**Body:** LLM summary → `## Caption` (verbatim text, **inside a fenced code block** — Obsidian renders markdown, and a verbatim `![](http://attacker/pixel.png)` in a caption would otherwise become a tracking beacon and `[text](http://phish)` a live link; fencing preserves every byte while defanging rendering) → `## Extracted` (transcript / OCR / article text, same fencing) → media embed. **Classification routing:** educational/reference → full note; entertainment → one-liner. **Idempotency:** stable id = sha256 of the canonical permalink (`/reel/`, `/reels/`, `/tv/` folded into `/p/`; date-free so the same post arriving via export AND fetch dedups to one note — decided 2026-07-23); permalink-less text notes hash text + timestamp; dedup ledger in `.sift/`.

---

## 7. Extraction pipeline (per modality)

- **Caption (every post — first-class, do this first).** Pulled as structured text from the post metadata (gallery-dl's `description` field; the DM/share JSON for `share_capture`/`dyi_export`). No OCR/STT needed — highest-signal, cheapest field. Even when media fetch fails, the caption alone often makes a useful note.
- **Images / carousels.** Loop all N images; **vision-LLM OCR** (Claude, tool-less call) — Instagram content (memes/screenshots/stylized) is out-of-domain for document OCR engines and home turf for a VLM (§13; decided 2026-07-22, replaces PaddleOCR + fallback). Output re-enters the same sanitize → interpreter path as any untrusted text. A local OCR engine is the documented swap if API cost or offline operation ever bites.
- **Reels / videos.** ffmpeg → freeflow / faster-whisper; major-language target; detect + record language + confidence; flag low-confidence. Defer non-English target translation (Whisper only translates *into* English).
- **Links.** trafilatura readable content + metadata (Mercury is dead).
- **On-screen/burned-in video text — DEFERRED.** Keyframe dedup + OCR.

Every extractor returns a uniform `Extraction { text, languages, confidence, artifacts }`. Extraction is **deterministic pipeline code — no model involved** (§5). Its output is untrusted text; it is sanitized and then handed to the quarantined tool-less **interpreter** agent (§8, `AGENTS.md`). Fetcher egress policy: gallery-dl/yt-dlp are scoped to `instagram.com` + its CDNs (`cdninstagram.com`, `fbcdn.net`) and may use the session cookies (that is their job); the link fetcher may hit any public URL but is **credential-less** (never sends cookies/auth), **blocks private/loopback addresses** (SSRF guard), and its response is always treated as untrusted.

---

## 8. Interpretation (the one quarantined agent)

The **interpreter** is the only agent in the system. It is an Agno agent with **no tools, no secrets, and no session memory — stateless per item** (untrusted content must never persist into future contexts): sanitized caption/OCR/transcript/link text in, a strict Pydantic `Interpretation` record out — classification, a 1–3 sentence summary, tags, candidate action items. Reasoning stays off by default (a 3-way classify + short summary doesn't need it; flip the flag if action-item recall measurably suffers). Off-schema output is rejected and logged, never retried into compliance by loosening. A hijacked interpreter can only emit a *well-formed* record — so downstream code **re-forces security-relevant values instead of trusting them**: every action item is re-initialized to `status: pending`, confidence is clamped, classification is a closed enum. Plain code (not a model) validates, writes the note (entertainment short-circuits to a one-liner), and updates the digest. PII is masked only on the model-API-bound copy; the vault note carries the verbatim caption from the `Item` (rendered inside a fenced block so embedded markdown can't fire — §6).

---

## 9. Action review (draft-then-approve)

Your headline want — "tell me if there's something I need to act on" — is the riskiest feature (unattended precision is the make-or-break; the space is contested/unproven). So v1 **proposes**, never alerts: `review/action_items.py` collects candidates `{text, due?, confidence, source_note}` (dues arrive as typed dates via the `Interpretation` schema) → a single **`Action Review.md`** you skim and confirm/dismiss. Only confirmed items ever become reminders. Optimize for **recall with visible confidence**, not silent precision. Later (Life Planner fold-in): confirmed items flow into the planner's calendar loop behind its approve-gate.

---

## 10. RAG / search

Agno **Knowledge** over **LanceDB** (local, embedded), hybrid search on; embedder swappable (OpenAI `text-embedding-3-small`, or a local Ollama embedder to stay fully offline). **Search is a deterministic index query — no agent, no model call** (decided 2026-07-22): `sift search "<q>"` runs the hybrid query directly and returns ranked notes + snippets + permalinks. This keeps the interpreter the only agent in the system and closes the delayed-injection re-entry path (a model re-reading verbatim vault captions at query time would bypass every ingest guardrail). **The embed-bound copy of note text is PII-masked with the same masker as the interpreter path** before it goes to a remote embedder — the vault itself stays verbatim. An answer-synthesis agent is added only if search ever needs to become chat (deferred, §3). Upgrade path: Postgres + PgVector if you ever want everything in one server. Vault-wide capability → transfers cleanly to Life Planner.

---

## 11. Folding into Life Planner

1. **Ingestion becomes scheduled.** The `cookie_fetch`/`share_capture` adapters run under a background Agno agent; `core.run()` unchanged.
2. **Notes are already native** (same vault + Life-Planner frontmatter) — no migration.
3. **Action items feed the planner** — confirmed items become tasks/goals; scheduling + notifications happen behind the planner's approve-gate; the dashboard renders the capture inbox + action review. "Writing new agents through the dashboard" = new Agno agents.

Design consequence held now: no core logic in `cli.py`; no assumption of a human at a terminal inside `core`/agents/tools.

---

## 12. Tech stack

| Concern | Choice (v1) | Notes |
|--------|-------------|-------|
| Language | Python 3.11+ | matches freeflow + ML ecosystem |
| **Agent framework** | **Agno v2** (pin `agno==2.7.x`+) | storage + RAG + reasoning + MCP; not LangChain. `AGENTS.md` |
| State | **dedup ledger** (SQLite) | the only durable state v1 needs; interpreter is stateless. Agno Db (sessions/memory) arrives with the first conversational agent (Life Planner fold-in) |
| Vector RAG | **LanceDB** (Agno Knowledge) | embedded, local; queried deterministically (§10) |
| Embedder | OpenAI `text-embedding-3-small`, or Ollama (local) | swappable; embed-bound text PII-masked (§10) |
| LLM | Claude (Anthropic provider) | interpreter + vision-OCR; reasoning off by default (§8) |
| Runtime | none — batch CLI (launchd/cron) | nothing in F0–F7 serves HTTP; a resident runtime (AgentOS) first earns its keep at Life Planner fold-in |
| Saved-posts ingest | **gallery-dl** (`--cookies-from-browser`) | fallback added only if/when the extractor breaks |
| New-item ingest | Share-Sheet / Shortcut → capture endpoint | zero-risk |
| Backfill/DMs | DYI export parser | stdlib zip/json |
| Audio | ffmpeg + freeflow / faster-whisper | reuse your STT |
| Image OCR | **vision-LLM (Claude)** | §7, §13; local OCR engine = documented swap |
| Links | trafilatura | not Mercury; credential-less fetch, SSRF-guarded (§7) |
| **Guardrails** | deterministic sanitize + Agno built-in PII mask + strict schema + value re-forcing | structural quarantine is the defense; Presidio/LLM Guard cut 2026-07-22 (`AGENTS.md` §3) |
| CLI | typer | thin shell |

---

## 13. Feasibility flags & mitigations (the truth section)

1. **No fully-safe automated ingestion exists.** The recurring `cookie_fetch` is unofficial and non-zero risk on your main account. **Mitigation:** read-only, weekly, one session, back off on 429; lean on the zero-risk `share_capture` + `dyi_export` adapters over time.
2. **Captions can lie / be sparse** but are still the best default signal; don't assume the caption == the content — reconcile with OCR/STT when present.
3. **OCR is not "solved" for Instagram content** (memes/screenshots/stylized/warped) — which is why v1 goes vision-LLM-only (§7): stylized meme text is the VLM's home turf, and personal volume makes per-image cost cents. Validate on your own images. Honest tradeoff: the VLM call ships the **image bytes** to the model provider (images can't be PII-masked the way text can) — accepted for v1; the documented swap is a local OCR engine or local VLM if that ever bothers you.
4. **Whisper multilingual is uneven** (low-resource, music-bed, overlap). Major-language target in v1; flag low-confidence; translation X→English only.
5. **Untrusted-content injection is a real threat.** Captions and shared links are a known indirect-prompt-injection vector. **Mitigation:** Rule of Two enforced structurally (the tool-less interpreter) + spotlighting + Unicode-tag stripping + credential-less fetches + strict output schema (`AGENTS.md`). Injection isn't fully solvable — the goal is to make a hijacked model powerless, not to catch every payload.
6. **Effort:** caption+links+carousels human-in-the-loop ≈ a couple of weekends; a trusted daily-driver ≈ ~1 month; robust unattended ≈ multi-month. Time-box v1.

---

## 14. Competitors & positioning

Unchanged from the verdict: closest adopt-instead is **Fabric** (rejected — cloud/closed, no action-review); direct organizers (Sorti/Vestron/Albo) are free but no reel STT/alerts; read-later is a graveyard (Pocket, Khoj Cloud dead); transcription is a commodity. Sift's edge is integration + local ownership + action-review + caption-first extraction — fine for a personal tool, not a business.

---

## 15. Build phases (shippable cuts)

- **F0 — Foundation.** Repo, config, Agno wired (a hello-world agent to prove the framework + version pin), `Item`/`Extraction`/`Interpretation`/`Note` types, vault writer + frontmatter (incl. fenced `caption`), dedup ledger (the only durable state — no Agno Db sessions/memory until a conversational agent exists), dry run on a fixture. *Done = a fake item → correct note.*
- **F0.5 — Guardrail harness.** Deterministic sanitize step (spotlight, strip `U+E0000–E007F`, neutralize md links; injection-pattern flagging log-only), built-in PII masking for everything model-API-bound, SSRF-safe fetcher egress policy (credential-less, pin-IP, per-hop redirect validation), the quarantined **tool-less, stateless interpreter** skeleton with strict output schema, and boundary value re-forcing (`status: pending`, clamped confidence). *Done = a caption with an injection string + a fake SSN is masked on the model-bound path, off-schema output is rejected, and a record claiming `status: confirmed` comes out `pending`.* See `AGENTS.md`.
- **F1 — Ingest: DYI export + captions.** `dyi_export.py` (saved + self-DMs + captions). *Done = your export backfills notes, captions populated.*
- **F2 — Ingest: cookie_fetch + captions.** gallery-dl saved-folder incremental fetch on your machine; media + caption metadata; weekly scheduler. *Done = a scheduled run pulls only new saves, read-only, backs off on 429.*
- **F3 — Extract: captions + carousels + links** through the deterministic router → sanitize → **interpreter** (strict schema) → deterministic writer → notes. *Done = a real carousel and a real link post become good notes.*
- **F4 — Reels STT.** freeflow/faster-whisper for major-language reels. *Done = an English reel transcribed; low-confidence flagged.*
- **F5 — RAG search** (Agno Knowledge + LanceDB; deterministic query, no agent; embed-bound text masked). *Done = find a note by content.*
- **F6 — Action review** digest. *Done = a real deadline surfaces; nothing fires unconfirmed.*
- **F7 — share_capture.** Share-sheet/shortcut → capture endpoint. *Done = tapping Share files a note in real time.*
- **v-next:** frame OCR, non-English translation, auto-linking, Data Portability API (if it ever fits), fold into Life Planner (scheduled agent + dashboard).

---

## 16. Definition of "v1 works"

You run your export **and** a scheduled `cookie_fetch` through it; your backlog lands as searchable notes with **captions populated**; `sift search` finds things by content; **Action Review** surfaces ≥1 real item you'd have missed; the guardrail tests pass (injection + PII neutralized); nothing fires without your confirmation. The real test: you'd be annoyed to lose it.

---

## 17. Open decisions

| Decision | Default | Note |
|---------|---------|------|
| Project name | Sift | rename freely |
| Agent framework | **Agno v2** (pinned) | §12, `AGENTS.md` |
| Pipeline shape | **One quarantined tool-less interpreter; deterministic extract/write** | decided 2026-07-22 — replaces the reader/enricher pair; §5, §8 |
| Search | **Deterministic LanceDB query — no search agent** | decided 2026-07-22 — closes delayed-injection re-entry; §10 |
| Embedder egress | **Embed-bound text PII-masked** (same masker as interpreter path) | decided 2026-07-22; §10 |
| Guardrail stack | **Sanitize + built-in PII mask + strict schema + value re-forcing**; Presidio & LLM Guard cut; injection-pattern filter log-only | decided 2026-07-22 — structural quarantine is the defense; `AGENTS.md` §3 |
| Storage | **Dedup ledger only**; interpreter stateless; Agno Db at Life Planner fold-in | decided 2026-07-22; §12 |
| Image OCR | **Vision-LLM default** (image bytes to provider = accepted tradeoff); a **local OpenAI-compatible backend** (Ollama + Baidu Unlimited-OCR) is the opt-in swap for testing/offline — free unlimited, bytes stay on-machine | decided 2026-07-22; local swap added 2026-07-24; §7, §13.3 |
| DB tech | SQLite (ledger) + LanceDB (RAG) | PgVector upgrade path |
| Recurring ingest | `cookie_fetch` weekly, read-only | daily ceiling; never hourly |
| DMs — new posts (recurring) | **`dm_fetch`** weekly, read-only Playwright, self-thread only | decided 2026-08-19; dedicated profile, headed off-screen, challenge → stop, never auto-solved; reuses cookie_fetch's cadence/back-off rig |
| DMs — history / backfill | `dyi_export` (occasional) | only route to already-sent DMs |
| DYI export format | **JSON only** — an HTML-format export is refused loudly with re-request-as-JSON guidance | decided 2026-07-23 — narrows checklist F1's "JSON + HTML shapes"; the export UI offers JSON, so an HTML parser is built only if a JSON export is ever genuinely unavailable |
| New items | `share_capture` share-sheet | zero-risk end state |
| Data Portability API | Deferred | registry-gated; no DM data type; US-uncertain |
| LLM provider | Claude, swappable (local optional) | strict schema |

---

## 18. Sources (load-bearing)

- Instagram Basic Display shutdown + professional-only successors: developers.facebook.com/docs/instagram-platform
- Meta Data Portability API + DTI Data Trust Registry (trust levels, audit, data types = photos/videos/media/social-posts/notes/archives; no messages): developers.facebook.com/docs/data-portability · dt-reg.org/about · dtinit.org
- Native "Transfer Your Information" = photos/videos only, schedulable: about.fb.com/news/2023/10/manage-your-information-across-apps
- Fabric as an approved EU transfer destination (recurring saved-post transfer): onfabric.substack.com
- gallery-dl Instagram `saved` extractor + `--cookies-from-browser` + metadata/caption + `--download-archive`: github.com/mikf/gallery-dl
- Agno v2 (storage/RAG/reasoning/MCP/teams; AgentOS free, Control Plane paid): docs.agno.com · github.com/agno-agi/agno
- Guardrails: agno.guardrails (PII/PromptInjection/Moderation, pre/post hooks, requires_confirmation); Microsoft Presidio; LLM Guard (protectai); Meta "Rule of Two"; gVisor/Firecracker (E2B/Modal)
- OCR/STT/links: PaddleOCR(-VL) · faster-whisper/WhisperX · trafilatura
