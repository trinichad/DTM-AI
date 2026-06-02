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
- **Vendor auth schemes are not interchangeable — confirm against the live tenant, never assume.**
  Kaseya ships in two incompatible API generations: VSA 9.5 (`/api/v1.0`, Bearer token via a `/auth`
  exchange) vs **VSA X / API v2** (`/vsa/api/v2/...`, HTTP **Basic** auth with a `TOKEN_ID:TOKEN_SECRET`
  pair, no token exchange). DTM AI was first ported with the 9.5 scheme; the live DTM tenant
  (`ks2.dtmconsulting.com`) is VSA X. Fix landed in `clients/kaseya.py` + `core/credentials.py`
  (spec keys `KASEYA_URL`/`KASEYA_TOKEN_ID`/`KASEYA_TOKEN_SECRET`) + `clients/scopes.py` (v2 prefixes).
  The Manage-keys form derives its fields from the CredentialSpec, so it tracks this automatically.
  v2 response envelopes vary by endpoint → `KaseyaClient._as_list` normalizes shapes, and the slimmed
  reads fall back to the raw row when expected field names are absent.
