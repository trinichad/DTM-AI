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
Hermes persists/curates its own learned skills (its memory + skills system). DTM AI's per-client
`memory.md` ([[memory-vault]]) complements that with human-readable client notes. A future DTM-side
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
- **Verify a reference build actually WORKS before porting its auth — a module that imports cleanly
  is not a module that authenticated.** The live DTM Kaseya tenant (`ks2.dtmconsulting.com`) is
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
