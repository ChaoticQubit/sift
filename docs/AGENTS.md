# AGENTS.md — Sift agent architecture & guardrail harness

How Sift's agents are built (Agno v2) and the safety harness every agent runs inside. This is the standard for **all** agents in this project, not just Sift. Read alongside `DESIGN.md`.

> **Harness engineering** = the deterministic scaffolding around a probabilistic model — guardrails, tool-gating, sandboxes, verification loops, observability. The model is the engine; the harness is the chassis, brakes, and seatbelts. Sift ingests **untrusted** content (Instagram captions and shared links are a known prompt-injection vector), so the harness is not optional.

---

## 1. Framework: Agno v2

- **Pin the version** (`agno==2.7.x` or later). Agno had a breaking v1→v2 rewrite — **ignore pre-2026 tutorials**, follow docs.agno.com.
- **Storage/memory:** one Agno `Db` over **SQLite** (local) holds agent sessions, cross-session user memory, and session summaries. Upgrade path: Postgres (unifies sessions + memory + PgVector RAG in one server) if ever wanted.
- **RAG/knowledge:** Agno **Knowledge** over **LanceDB** (embedded, local), hybrid search on, agentic retrieval (agent decides when to fetch). Embedder swappable (`text-embedding-3-small`, or Ollama local).
- **Reasoning:** give agents `ReasoningTools` (think/analyze) and/or a reasoning model; Claude is first-class via the Anthropic provider.
- **Tools:** in Sift v1, agents hold **no tools** (§2) — capability is pipeline code. For future acting agents: plain typed `@tool` Python functions only; external integrations via `MCPTools`/`MultiMCPTools`; web research via built-in toolkits (Tavily/Exa/DuckDuckGo) when needed.
- **Composition:** Sift v1 needs none — the pipeline is deterministic Python around one agent call. For future multi-agent features: a **Team** (delegating leader + specialist members) or a **Workflow** (deterministic step pipeline) — prefer Workflow so trust boundaries stay explicit (§2).
- **Runtime:** self-host the free **AgentOS** FastAPI runtime with full run logging. **Do not** pay for the hosted Control Plane ($150/mo) initially.
- **Ceiling caveat:** if a future feature needs durable, heavily-branching, deterministic resumable orchestration, prototype in Agno **Workflows** first; only reach for LangGraph if you hit Agno's control ceiling. Don't pre-split the stack.

---

## 2. The Rule of Two (the core safety architecture)

Meta's "Rule of Two" (same family as the dual-LLM / CaMeL pattern): an agent context may combine at most two of **{untrusted input, dangerous capabilities, secrets}**. Prompt injection is unsolved, so design assuming the model that reads untrusted text **will** eventually be hijacked — and make sure that when it happens it has no levers to pull.

Sift enforces this **structurally**, not with a second agent (decided 2026-07-22; the earlier reader/enricher pair put summarization — i.e. reading untrusted text — in the same context as write tools, which is exactly what the rule forbids):

**The `interpreter` (QUARANTINED) — the only model that touches untrusted content.**
- Input: sanitized caption/OCR/transcript/link text (§5) — still natural language, still assume poisoned.
- **NO tools. NO secrets in context.** It cannot fetch, write, tag, or execute anything.
- Output: a strict Pydantic `Interpretation` record (classification, summary, tags, action-item candidates). Off-schema output is rejected and logged.
- A hijacked interpreter can only emit a weird record — which schema validation catches.

**Everything with capability is deterministic code, not a model.**
- Extraction (`extract/router.py` → OCR/STT/link fetch) is plain code dispatched by `Item.medium` — choosing "video → STT" needs no intelligence, so no LLM sits in that loop and no fetch is ever attacker-triggerable via caption text.
- Vault writes, tagging, dedup, and the digest run as plain code on the **schema-validated** record. No agent holds `save_item`/`tag_item`-style write tools in Sift v1.

When Life Planner later needs an agent that *acts* (calendar writes, notifications), it is added **then**, and it operates only on typed records — never on raw untrusted text — with `requires_confirmation` on its writes (§4).

---

## 3. Guardrail layer — Agno `pre_hooks` / `post_hooks`

Guardrails in Agno are hooks on every `Agent`/`Team` that raise `InputCheckError` / `OutputCheckError`. Order them cheap→expensive.

```python
from agno.guardrails import PromptInjectionGuardrail, PIIDetectionGuardrail
# custom guardrails subclass agno.guardrails.BaseGuardrail

interpreter = Agent(
    name="interpreter",
    model=...,
    # NO tools — this agent only interprets. Capability lives in pipeline code.
    reasoning=True,
    output_schema=Interpretation,            # strict; off-schema output rejected
    pre_hooks=[
        PromptInjectionGuardrail(),          # cheap: known injection patterns
        PIIDetectionGuardrail(mask=True),    # Agno built-in (regex) — first pass
        PresidioGuardrail(),                 # custom BaseGuardrail wrapping Presidio (real recall)
        UntrustedContentGuardrail(),         # spotlight/delimit + strip Unicode tag chars (§5)
    ],
    post_hooks=[SensitiveOutputGuardrail()], # LLM Guard Sensitive + MaliciousURLs on OUTPUT
)
```

### (a) PII — prevent leakage/exfiltration
- **Scope (decided 2026-07-22): masking applies to the LLM-bound copy of the text only.** The vault note is written deterministically from the `Item` and keeps your data **verbatim** — it is your own content on your own disk; masking it would destroy what you saved on purpose. PII guardrails protect what leaves the machine (model APIs) and what the model emits.
- **Input:** `PIIDetectionGuardrail(mask=True)` (Agno, regex — fast first pass) **+** a custom `PresidioGuardrail(BaseGuardrail)` wrapping Microsoft **Presidio** (`AnalyzerEngine` + `AnonymizerEngine`, 100+ entity types; add `PatternRecognizer`s for any app-specific IDs) for real recall.
- **Output:** a `post_hook` wrapping **LLM Guard**'s `Sensitive` + `MaliciousURLs` output scanners — catches PII the model itself emits, before it leaves. Raise `OutputCheckError` to block.
- **Egress (decided 2026-07-22 — split by trust, not one global allowlist):**
  - *Agent context:* the interpreter has **no tools**, so its only egress is the model API call itself. Nothing an injected caption says can cause a fetch.
  - *`gallery-dl` / `yt-dlp`:* scoped to `instagram.com` + its CDNs (`cdninstagram.com`, `fbcdn.net`); session cookies allowed — that is their job.
  - *Link fetcher (trafilatura):* may hit any public URL, but is **credential-less** (never sends cookies/auth headers), **blocks private/loopback/link-local addresses** (SSRF guard), and its response is always treated as untrusted input.
  - No long-lived secrets in agent context; short-lived, audience-scoped tokens only.

### (b) Restrict terminal / command usage — architectural, not a filter
- **In Sift v1, no agent holds tools at all.** The interpreter is tool-less; extraction and writes are pipeline code. There is nothing to gate because there is nothing to call.
- For any **future** acting agent (Life Planner): **never register a shell tool, code interpreter, or `run_command` tool.** Agno tools are typed functions; expose only narrow parameterized ones. Gate every **side-effecting** tool with `requires_confirmation=True`. On `RunResponse.is_paused`, resolve via `requirement.confirm()/reject()` then `agent.continue_run(...)`. For unattended runs, auto-approve **only** an explicit allowlist of low-risk read tools; escalate the rest.
- If any broad capability is ever kept, add an advisory `PreToolUse` deny check (`rm -rf`, `sudo`, `chmod 777`, `curl|wget`, `| sh`) as a custom guardrail — but treat it as advisory; the sandbox (c) is the real boundary.

### (c) Block malicious scripts
- **Default: no code-exec tool at all → nothing to exploit.**
- If code execution is ever unavoidable, run it in a **tiered sandbox** by trust: gVisor (`runsc`) for your own generated code; a **Firecracker microVM** (E2B / Modal / Vercel Sandbox) for anything derived from untrusted content. Enforce: **no network, read-only FS, non-root, seccomp + `PR_SET_NO_NEW_PRIVS`, isolated /proc, CPU/mem/time limits.**
- Add LLM Guard `BanCode` / `Secrets` **input** scanners to flag code or keys embedded in captions before they reach anything.

---

## 4. Human-in-the-loop (`requires_confirmation`)

In Sift v1 the human-in-the-loop surface is **`Action Review.md`** (draft-then-approve, `DESIGN.md` §9): the pipeline *proposes* action items; only you confirm them. No agent-held write tool exists to gate. When a future acting agent gets side-effecting tools, they pause the run for approval via `requires_confirmation` (§3b). For the weekly `cookie_fetch`, anything that would touch the account is not just gated — it's **not implemented** (read-only rule).

---

## 5. Untrusted-content hardening (applies to all Instagram text)

- **Spotlighting:** in the interpreter's prompt, wrap every caption/transcript/link body in randomized delimiters and label it explicitly as **untrusted DATA, never instructions**.
- **Sanitize deterministically before text enters context:** strip Unicode tag chars `U+E0000–U+E007F`, neutralize markdown image/link syntax, normalize whitespace.
- **Provenance:** tag every item with source URL + timestamp + `ingest_via` + trust tier, carried through to the note.
- **Self-verification:** a post-generation schema-validation step on the interpreter's structured output (reject anything off-schema, log the rejection).

---

## 6. Observability

Run under **AgentOS** with full run logging. Log every tool invocation with its provenance chain (which item, which source, which trust tier). Keep tools few and strong. Every guardrail block is logged, not silently swallowed.

---

## 7. Minimum-viable harness (if shipping fast)

The **tool-less interpreter + strict output schema** (the structural core — non-negotiable) + `pre_hooks=[PromptInjectionGuardrail(), PIIDetectionGuardrail(mask=True)]` + LLM Guard `Sensitive` post-hook + credential-less link fetches. Add Presidio-in-`BaseGuardrail`, the private-IP block, and a gVisor/Firecracker sandbox as the hardening pass.

---

## 8. Standard for all project agents

Every agent built in this project (Sift today; Life Planner agents; anything authored via the dashboard later) uses: **Agno v2**, persistent storage (SQLite/Postgres), RAG where needed (LanceDB/PgVector), reasoning, and **this guardrail harness** — PII masked on LLM-bound text, no generic shell/command tool, human-approval on writes, sandbox for any code-exec, and the Rule of Two whenever untrusted external content is ingested: **the model that reads untrusted text holds no tools and no secrets; capability lives in deterministic code (or a separate agent that only ever sees typed records).**
