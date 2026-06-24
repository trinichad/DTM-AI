# SOP — Skill Model: Primitives vs Learned Skills (A-layer)

> Implements D-15. The defining architecture decision: the owner does NOT hand-code tools;
> capabilities grow as **learned skills** that compose a small, trusted, guarded set of **primitives**.

## The two layers
| Layer | What | Who creates it | Grows |
|---|---|---|---|
| **Primitives** | Guarded low-level capabilities that touch client systems (hold creds, decide read/write). The security boundary. | Trusted code, built once (and when a new vendor is added). NOT AI-improvised. | Rarely |
| **Learned skills** | Reusable procedures the AI/Hermes composes from *enabled* primitives, saved for later. | The AI (Hermes), automatically. No human coding. | Constantly |

## Why this is safe
A learned skill can ONLY combine primitives that are already enabled. It cannot invent new
low-level access. So the AI may freely learn/save/reuse skills, and the worst case is combining
capabilities the owner already permitted — read-only by default, tenant-scoped, audited, reversible.
Human control lives at the **primitive layer** (the Capability Console), not per learned skill.

This is why "no hand-made tools / all learned skills" coexists with "security is the highest priority."

## The primitive layer today: scoped read connectors (D-15)
`kaseya_read` / `cylance_read` / `huntress_read` (`execution/skills/*_read.py` + `clients/scopes.py`):
- accept an arbitrary `path` but enforce **GET-only + a per-vendor read-path allowlist**
- block auth/token endpoints, host escape (`://`, `//`, `..`), and anything outside the allowlist
- a blocked path NEVER reaches the vendor client (proven in `tests/test_scopes.py`)

Result: Hermes can compose ANY allow-listed read into a learned skill with zero new code, but the
reachable surface is fixed, read-only, and scoped. Widening reach = adding a prefix in
`clients/scopes.py` (reviewed config), never AI-improvised.

Curated slimmed reads (`kaseya_list_assets`, etc.) remain as convenient, well-described primitives
the model prefers for common tasks.

## Writes
Writes are SEPARATE, individually-gated primitives (never part of a generic connector). Each write
primitive is opened per-tool in the Capability Console (`allow_write` + `require_approval`), and the
approval workflow (pending) mints one-shot tokens. Destructive always needs per-action approval (floor).

## Reconciling D-4
D-4 ("human merge for every generated tool") was about generated *Python primitives* that could reach
client systems in new ways. In this model, learned skills are **compositions, not new primitives** —
no new code, no merge gate; they're bounded by the enabled primitive set. Human control moved UP to the
primitive/Capability layer. A brand-new *primitive* (new vendor connector / new write op) is still
trusted code added deliberately — but by the maintainer, not hand-coded by the owner during operations.

## Where learned skills live
Hermes persists/curates its own learned skills (its memory + skills system). MSP AI's per-client
`memory.md` ([[memory-vault]]) complements that with human-readable client notes. A future platform-side
"skill library" view can surface/disable saved skills if desired.

## Edge cases / lessons
- Prefix matching is boundary-aware (`/account` ≠ `/account_settings`) — see `_matches` in scopes.py.
- Generic connectors return raw payloads; dispatch() truncates to 20k before the model sees them.
  If a vendor read returns large PII blobs, prefer a curated slimmed primitive for that path.
- New vendor onboarding = creds (config) + a scoped connector + its allowlist (one-time code), then
  unlimited learned reads on top.
- **Paginate by the API's own page-count, not just "a short page."** The Fleet card once showed a bogus
  10,000 Cylance devices = exactly `page_size 200 × max_pages 50`. Cylance's `/devices/v2` kept returning
  *full* pages, so terminating only on `len(items) < page_size` never tripped and the loop ran to the cap.
  Fix (mirrors the proven Kaseya Link client): stop when `page_number >= total_pages` (fall back to the
  short-page rule only when the API gives no `total_pages`), with `max_pages` as a hard safety stop.
  Regression: `tests/test_clients.py::test_paginate_stops_on_total_pages_not_short_page`. Corollary: the
  *chat* can UNDER-count the same data because tool results are truncated (`MAX_RESULT_CHARS`) before the
  model counts them — the dashboard's server-side `len(list)` is the authoritative count.
- **Dedup paginated lists by their unique id — pagination DRIFTS.** After the total_pages fix, the Fleet
  card showed 1800 Cylance devices (= 9 full pages × 200) when the real count was 1708. Cause: Cylance's
  device list shifts while you page it, so records near page boundaries get returned on two consecutive
  pages — a raw count over-reports by the overlap. Fix: the list skills dedup by the record's unique key
  (`cylance_list_devices` by `id`, `cylance_list_threats` by `sha256`) keeping first-seen order. This is
  the authoritative count even with overlap or a padded last page. Regression:
  `tests/test_skills_integration.py::test_cylance_devices_dedup_pagination_drift`. General rule: any
  paginated vendor list whose underlying set mutates during the read must be deduped by stable id, not
  trusted to be disjoint across pages.
- **Cylance's pagination REQUEST param is `page`, not `page_number` (the response only ECHOES
  `page_number`).** Sending `page_number` as the request param made Cylance ignore it and return page 1
  on every iteration — so every page was identical (raw 1800, 200 after dedup). The ported Kaseya Link
  client had the same latent bug; it never surfaced because that build read its count from the response's
  `total_number_of_items` field (its probe reports exactly that), never actually enumerating all devices.
  Fix: `clients/cylance.py` `get_paginated` sets `params["page"]` (and keeps `page_number` for safety —
  unknown query params are ignored). Regression: `tests/test_clients.py::test_paginate_sends_page_request_param`.
  Lesson: distinguish a vendor's request params from the fields it echoes in responses — they are NOT
  always the same name, and a count that "looks plausible" (1708) can come from a total field while real
  enumeration is silently broken.
- **Verify a reference build actually WORKS before porting its auth — a module that imports cleanly
  is not a module that authenticated.** The live the Kaseya tenant (`vsa.example.com`) is
  **VSA 9.5**: REST API at `/api/v1.0/`, auth = plain Basic `base64(KASEYA_USER:KASEYA_PASS)` →
  `GET /api/v1.0/auth` → `Result.Token` → `Bearer` for subsequent calls (a static `KASEYA_TOKEN`
  bearer also works). This is the scheme the proven **Kaseya Link** build uses.
  A second build (`msp-ai-ops`) used `KASEYA_TOKEN_ID:KASEYA_TOKEN_SECRET` Basic against `/vsa/api/v2/...`
  — that path hits the VSA *web UI* ("Whoops." HTML) and `/api/v1.0/auth` rejects the pair
  (`ResponseCode 4010001`); it **never actually authenticated** (its `"✅ loaded"` print fires at import,
  not after a call). Lesson: confirm an endpoint returns real data, and prefer the build you can prove,
  not the one that merely loads. Canonical client: `execution/clients/kaseya.py`; spec keys
  `KASEYA_URL`/`KASEYA_USER`/`KASEYA_PASS`/`KASEYA_TOKEN` in `core/credentials.py` (the Manage-keys form
  derives its fields from the spec, so it tracks this automatically).
- **Kaseya: agents ≠ assets, and large lists must not truncate silently.** The agent reported
  machines (iwr-02/lt05/lt06) as nonexistent though they were in the Kaseya machine group. Two causes,
  both fixed: (1) we only exposed `/assetmgmt/assets` (asset-management records); the machine-group
  view is `/assetmgmt/agents` (a machine can be a managed *agent* with no asset record). Added
  `KaseyaClient.get_agents()` + the `kaseya_list_agents` skill (the proven Kaseya Link client always
  had `get_agents`). (2) After the owner widened the API account's scope, Kaseya jumped ~28→225 assets,
  past the ~20k-char tool-result cap — and a **blind string-cut made the model believe it saw the whole
  list**, so it falsely concluded items were absent. Fix: `agent.tool_payload()` caps a long `data`
  list by *rows* and attaches an explicit `_truncated` note ("PARTIAL RESULT… re-call with a
  name_contains filter"), and both list skills gained a `name_contains` substring filter so focused
  queries return complete results. Lesson: never let a size cap silently hide rows from a model that's
  about to assert completeness (Behavioral Rule #2); give it a filter and tell it when it's seeing a
  partial view. Regression: `tests/test_agent.py::ToolPayload`,
  `tests/test_skills_integration.py::test_kaseya_list_agents_and_filter`.

## Amendment (2026-06-24, D-111) — the `bulk` meta-tool: one call, many runs

**Problem.** The agent fanned out the SAME tool once per item (52× `exo_grant_folder_access`, N×
`m365_list_users`), which burns the per-turn tool-call budget — it literally hit "reached the tool-call
limit without a final answer" — and reads as broken even when each call succeeds. Native list params
(D-110) fix the hottest tools one at a time, but the owner wanted EVERY bulk-able tool covered.

**Solution — a universal meta-tool handled in `dispatch()`.** `bulk(tool="<name>", items=[{…},{…}])`
runs `<name>` once per item in a SINGLE tool call. It is registered like any skill
(`skills/bulk.py`, source `msp_ai`, CATEGORY `read`) but `dispatch()` intercepts it (step 3b) and, for
each item, RE-ENTERS `dispatch()` for the inner tool. Therefore bulk grants **no new authority** — every
per-item run passes the full stack again: schema validation, the I-4 kill switch, CATEGORY + approval
gating, tenant isolation, and a per-item audit record. The meta-tool being `read` is not a hole: it never
executes anything itself; the inner tool's real category is gated on each recursion. Aggregated result:
`{tool, count, ok_count, error_count, results:[{index, ok, data|error}]}`. Bounded at 200 items
(`BULK_MAX_ITEMS`); nesting (`tool="bulk"`) and unknown inner tools are refused.

**Approval (reuses D-59 verbatim, honors the Rule #1 floor).** An item that auto-approves (trusted
write, or a live D-59 batch grant) runs inline — so a fleet of autonomous writes collapses to one call.
An item that needs human sign-off makes the inner `dispatch()` return `pending_approval`; bulk surfaces
THAT one card and stops (no orphan pile-up — D-47), carrying progress in a `bulk` block. Re-invoke bulk
after the owner decides to continue; already-applied items re-run harmlessly because the underlying tools
verify/self-heal and treat "already in that state" as success. Destructive tools still require their
per-action approval (they can never be batch-granted) — so a destructive bulk is one tool call but one
approval per deletion, by design.

**Steering.** The base system prompt now forbids repeating a tool for a list and directs the model to a
native list param first, else `bulk`. The `bulk` DESCRIPTION says the same. Native params remain the
preferred surface where they exist (cleaner schema + shared preflights); bulk is the universal fallback
that makes EVERY tool — including ones added later — batchable. Tests: tests/test_dispatch.py
(read fan-out, per-item validation, per-item write-gate enforcement, trusted-write batch, approval-pause,
nesting/unknown refusal, item cap).
