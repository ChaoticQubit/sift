# AGENTS.md — Sift agent architecture & guardrail harness

How Sift's agents are built (Agno v2) and the safety harness every agent runs inside. This is the standard for **all** agents in this project, not just Sift. Read alongside `DESIGN.md`.

> **Harness engineering** = the deterministic scaffolding around a probabilistic model — guardrails, tool-gating, sandboxes, verification loops, observability. The model is the engine; the harness is the chassis, brakes, and seatbelts. Sift ingests **untrusted** content (Instagram captions and shared links are a known prompt-injection vector), so the harness is not optional.

---

## 1. Framework: Agno v2

- **Pin the version** (`agno==2.7.x` or later). Agno had a breaking v1→v2 rewrite — **ignore pre-2026 tutorials**, follow docs.agno.com.
- **Storage/memory (decided 2026-07-22):** Sift v1 keeps only a **dedup ledger** (SQLite). The interpreter is a stateless per-item transform — session memory would persist untrusted content into future contexts (an injection-lifetime extension), and no v1 agent has a conversation to remember. Agno `Db` (sessions + user memory + summaries) arrives in the commit that creates the first genuinely conversational agent (Life Planner fold-in); upgrade path then: Postgres (unifies sessions + memory + PgVector RAG).
- **RAG/knowledge:** Agno **Knowledge** over **LanceDB** (embedded, local), hybrid search on, **queried deterministically — no retrieval agent in v1** (`DESIGN.md` §10). Embedder swappable (`text-embedding-3-small`, or Ollama local); the embed-bound copy of note text is PII-masked with the same masker as the interpreter path.
- **Reasoning:** Claude is first-class via the Anthropic provider. Reasoning stays **off** on the interpreter by default (a 3-way classify + short summary doesn't need it; one constructor flag if recall ever measurably suffers). `ReasoningTools` are tools — never attach them to a quarantined agent whose invariant is "zero tools".
- **Tools:** in Sift v1, agents hold **no tools** (§2) — capability is pipeline code. For future acting agents: plain typed `@tool` Python functions only; external integrations via `MCPTools`/`MultiMCPTools`; web research via built-in toolkits (Tavily/Exa/DuckDuckGo) when needed.
- **Composition:** Sift v1 needs none — the pipeline is deterministic Python around one agent call. For future multi-agent features: a **Team** (delegating leader + specialist members) or a **Workflow** (deterministic step pipeline) — prefer Workflow so trust boundaries stay explicit (§2).
- **Runtime:** none in v1 — Sift is a batch CLI driven by launchd/cron; nothing in F0–F7 serves HTTP. A resident runtime (self-hosted free **AgentOS**; never the $150/mo hosted Control Plane) first earns its keep at Life Planner fold-in.
- **Ceiling caveat:** if a future feature needs durable, heavily-branching, deterministic resumable orchestration, prototype in Agno **Workflows** first; only reach for LangGraph if you hit Agno's control ceiling. Don't pre-split the stack.

---

## 2. The Rule of Two (the core safety architecture)

Meta's "Rule of Two" (same family as the dual-LLM / CaMeL pattern): an agent context may combine at most two of **{untrusted input, dangerous capabilities, secrets}**. Prompt injection is unsolved, so design assuming the model that reads untrusted text **will** eventually be hijacked — and make sure that when it happens it has no levers to pull.

Sift enforces this **structurally**, not with a second agent (decided 2026-07-22; the earlier reader/enricher pair put summarization — i.e. reading untrusted text — in the same context as write tools, which is exactly what the rule forbids):

**The `interpreter` (QUARANTINED) — the only agent, and one of exactly two tool-less model touchpoints on untrusted content** (the other: the F3 vision-OCR call, image → text, whose output re-enters the same sanitize path).
- Input: sanitized caption/OCR/transcript/link text (§5) — still natural language, still assume poisoned.
- **NO tools. NO secrets in context. NO session memory — stateless per item** (persisted memory would carry a poisoned item's content into every later context).
- Output: a strict Pydantic `Interpretation` record (classification, summary, tags, action-item candidates). Off-schema output is rejected and logged.
- Schema validates **shape, not honesty** — a hijacked interpreter can still emit a *well-formed* record. So the boundary code **re-forces security-relevant values**: every action item is re-initialized to `status: pending` (never trusted from the model), confidence is clamped to [0,1], classification is a closed enum. The model proposes; deterministic code disposes.

**Everything with capability is deterministic code, not a model.**
- Extraction (`extract/router.py` → OCR/STT/link fetch) is plain code dispatched by `Item.medium` — choosing "video → STT" needs no intelligence, so no LLM sits in that loop and no fetch is ever attacker-triggerable via caption text.
- Vault writes, tagging, dedup, and the digest run as plain code on the **schema-validated** record. No agent holds `save_item`/`tag_item`-style write tools in Sift v1.

When Life Planner later needs an agent that *acts* (calendar writes, notifications), it is added **then**, and it operates only on typed records — never on raw untrusted text — with `requires_confirmation` on its writes (§4).

---

## 3. Guardrail layer — Agno `pre_hooks` / `post_hooks`

Guardrails in Agno are hooks on every `Agent`/`Team` that raise `InputCheckError` / `OutputCheckError`. Order them cheap→expensive.

```python
from agno.guardrails import PIIDetectionGuardrail
# custom guardrails subclass agno.guardrails.BaseGuardrail

interpreter = Agent(
    name="interpreter",
    model=...,
    # NO tools — this agent only interprets. Capability lives in pipeline code.
    # NO session memory — stateless per item (§2).
    output_schema=Interpretation,            # strict; off-schema output rejected
    pre_hooks=[
        PIIDetectionGuardrail(mask=True),    # Agno built-in — masks the LLM-bound copy;
                                             # idempotent, so it is safe as the one hook
    ],
)
```

**Single owner (decided F0.5):** the deterministic pipeline (`interpret()`) applies sanitize + spotlight + injection-flag + PII mask exactly once before `agent.run`; only the idempotent PII hook is also wired on the agent. Wiring the content-rewriting guardrails (`UntrustedContentGuardrail`, `InjectionFlagGuardrail` — both still live in `guardrails/injection.py` for reuse) as pre_hooks too double-applied the transforms and nested spotlight fences on the live prompt. Injection flagging stays LOG-ONLY: it never blocks — blocking would silently drop saved posts that merely quote injection phrasing (memes), and the structural quarantine already makes payloads inert.

**Stack rationale (decided 2026-07-22).** Presidio and LLM Guard were cut. Presidio duplicated the built-in masker's failure-mode coverage for this data (structured PII — emails/phones — in your own saved captions) while its NER masking of PERSON/LOCATION would destroy the creator/place names that *are* the content; LLM Guard was a torch-sized dependency scanning an output whose only destination is a local note sitting next to the verbatim caption. The defense that matters is structural: tool-less model, strict schema, value re-forcing, deterministic sanitize.

### (a) PII — prevent leakage/exfiltration
- **Scope (decided 2026-07-22): masking applies to every model-API-bound copy of text — the interpreter's input AND the embedder's input (`DESIGN.md` §10) — and nothing else.** The vault note is written deterministically from the `Item` and keeps your data **verbatim** — it is your own content on your own disk; masking it would destroy what you saved on purpose. (The F3 vision-OCR call ships image bytes, which can't be text-masked — accepted tradeoff, `DESIGN.md` §13.3.)
- **Masker:** `PIIDetectionGuardrail(mask=True)` (Agno built-in) as the hook, with the same masking function exposed as a plain callable (`guardrails/pii.py`) so the RAG embed path can reuse it outside an agent context.
- **Output:** the writer emits model-produced fields (summary/tags/action text) as escaped plain text — no rendered markdown from model output. If a post-check is ever wanted, rerun the same regex masker on the summary; no new dependency.
- **Egress (decided 2026-07-22 — split by trust, not one global allowlist):**
  - *Agent context:* the interpreter has **no tools**, so its only egress is the model API call itself. Nothing an injected caption says can cause a fetch.
  - *`gallery-dl` / `yt-dlp`:* scoped to `instagram.com` + its CDNs (`cdninstagram.com`, `fbcdn.net`); session cookies allowed — that is their job.
  - *Link fetcher (trafilatura):* may hit any public URL, but is **credential-less** (never sends cookies/auth headers) and SSRF-guarded. The guard must be TOCTOU-safe: **resolve DNS once, validate the resolved address, and connect to that pinned IP** (a low-TTL attacker record defeats check-then-refetch); **redirects are not auto-followed — each hop is re-validated** (a public URL 302ing to `http://169.254.169.254/` or `127.0.0.1` is the classic bypass); the blocklist covers IPv4 private/loopback/link-local **and** `0.0.0.0`, IPv6 (`::1`, `fc00::/7`, `fe80::/10`, `::ffff:0:0/96` mapped forms, **and every IPv6 form embedding an IPv4 destination — the NAT64 well-known prefix `64:ff9b::/96` and deprecated IPv4-compatible `::a.b.c.d` — decoded and re-checked as the embedded IPv4**; a local DNS64/NAT64 gateway would otherwise translate a "global" `64:ff9b::a9fe:a9fe` straight to `169.254.169.254`), and non-decimal IP encodings. Response is always treated as untrusted input.
  - *Enforcement, not assertion:* the gallery-dl/yt-dlp Instagram scope is enforced in code at F2 (yt-dlp invoked only on `instagram.com` permalinks; fetched-URL host check against the allowlist), not assumed from tool defaults.
  - No long-lived secrets in agent context; short-lived, audience-scoped tokens only.

### (b) Restrict terminal / command usage — architectural, not a filter
- **In Sift v1, no agent holds tools at all.** The interpreter is tool-less; extraction and writes are pipeline code. There is nothing to gate because there is nothing to call.
- For any **future** acting agent (Life Planner): **never register a shell tool, code interpreter, or `run_command` tool.** Agno tools are typed functions; expose only narrow parameterized ones. Gate every **side-effecting** tool with `requires_confirmation=True`. On `RunResponse.is_paused`, resolve via `requirement.confirm()/reject()` then `agent.continue_run(...)`. For unattended runs, auto-approve **only** an explicit allowlist of low-risk read tools; escalate the rest.
- If any broad capability is ever kept, add an advisory `PreToolUse` deny check (`rm -rf`, `sudo`, `chmod 777`, `curl|wget`, `| sh`) as a custom guardrail — but treat it as advisory; the sandbox (c) is the real boundary.

### (c) Block malicious scripts
- **Default: no code-exec tool at all → nothing to exploit.**
- If code execution is ever unavoidable, run it in a **tiered sandbox** by trust: gVisor (`runsc`) for your own generated code; a **Firecracker microVM** (E2B / Modal / Vercel Sandbox) for anything derived from untrusted content. Enforce: **no network, read-only FS, non-root, seccomp + `PR_SET_NO_NEW_PRIVS`, isolated /proc, CPU/mem/time limits.**
- Code embedded in a caption is inert against a tool-less interpreter — that inertness is the point of the structural Rule of Two; no code-scanner dependency is needed for it.

---

## 4. Human-in-the-loop (`requires_confirmation`)

In Sift v1 the human-in-the-loop surface is **`Action Review.md`** (draft-then-approve, `DESIGN.md` §9): the pipeline *proposes* action items; only you confirm them. No agent-held write tool exists to gate. When a future acting agent gets side-effecting tools, they pause the run for approval via `requires_confirmation` (§3b). For the weekly `cookie_fetch`, anything that would touch the account is not just gated — it's **not implemented** (read-only rule).

---

## 5. Untrusted-content hardening (applies to all Instagram text)

- **Spotlighting:** in the interpreter's prompt, wrap every caption/transcript/link body in randomized delimiters and label it explicitly as **untrusted DATA, never instructions**.
- **Sanitize deterministically before text enters context:** strip Unicode tag chars `U+E0000–U+E007F`, neutralize markdown image/link syntax, normalize whitespace.
- **Provenance:** tag every item with source URL + timestamp + `ingest_via` + trust tier, carried through to the note.
- **Self-verification + value re-forcing:** a post-generation schema-validation step on the interpreter's structured output (reject anything off-schema, log the rejection), then deterministic re-forcing of security-relevant values — `status: pending` re-initialized, confidence clamped, classification enum-checked (§2).

---

## 6. Observability

Stdlib logging + the `RunReport` counters (`processed/written/skipped/media_missing/guardrail_blocks/errors`) are v1's observability — right-sized for a batch pipeline. Log every model call and fetch with its provenance chain (which item, which source, which trust tier). Every guardrail block or schema rejection is logged, not silently swallowed. AgentOS run logging arrives with the resident runtime at Life Planner fold-in (§1).

---

## 7. Minimum-viable harness (if shipping fast)

The **tool-less, stateless interpreter + strict output schema + value re-forcing** (the structural core — non-negotiable) + the deterministic sanitize + `PIIDetectionGuardrail(mask=True)` + credential-less link fetches. Add the full SSRF guard (pin-IP, per-hop redirect validation, IPv6/encoded ranges) and a gVisor/Firecracker sandbox (only if code-exec ever appears) as the hardening pass.

---

## 8. Standard for all project agents

Every agent built in this project (Sift today; Life Planner agents; anything authored via the dashboard later) uses: **Agno v2**, persistent storage **where the agent actually has sessions to persist** (SQLite/Postgres), RAG where needed (LanceDB/PgVector), and **this guardrail harness** — PII masked on everything model-API-bound, no generic shell/command tool, human-approval on writes, sandbox for any code-exec, and the Rule of Two whenever untrusted external content is ingested: **any model touching untrusted content holds no tools and no secrets; capability lives in deterministic code (or a separate agent that only ever sees typed records, with its security-relevant values re-forced at the boundary).**
