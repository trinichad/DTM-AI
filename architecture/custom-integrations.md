# SOP — Custom Integrations (owner-defined, D-27)

> The owner can connect ANY vendor with an API from the dashboard — no code release needed.
> A custom integration is **metadata** (what fields, where they go on the wire, which paths are
> readable); the **secrets** live in the SecretStore exactly like built-in integrations (I-2/I-3),
> and the **capabilities** are built afterwards through the existing Build sandbox (I-5).

## What it is

A custom integration record describes:

| field | meaning |
|---|---|
| `id` | snake_case unique id (becomes the env-key prefix and the `SOURCE` for tools) |
| `label` | display name in the UI |
| `auth_kind` | `api_key` \| `login` \| `custom` — picks the default field template in the builder form |
| `fields` | owner-named credential fields → each derives an env key `<ID>_<FIELD-SLUG>`; `secret` fields render as password inputs and only ever show fingerprints |
| `base_url` | the API root, e.g. `https://api.vendor.com/v2` |
| `auth` | where credentials go on the wire: `bearer` (Authorization: Bearer <field>), `basic` (user+pass fields), `header` (custom header name + field), `query` (param name + field), or `none` |
| `read_paths` | allowlist of URL path prefixes that `scoped_read` may GET — **empty = nothing readable (fail closed)** |
| `probe_path` | optional GET path used by the Test button (defaults to the first read path) |
| `docs_url`, `notes` | reference links / owner notes |

Storage: `vault/integrations.json` — **metadata only, never secrets** (git-trackable, I-6).
Module: `execution/core/custom_integrations.py` (stdlib-only store + validation).

## How it plugs into the existing invariants

- `credentials.spec_for(name)` resolves built-in `SPECS` first, then the custom store — so
  `require()`, `set_integration()`, `status()`, `/api/integrations/*/fields` all work unchanged.
  Custom specs surface with `group="custom"`.
- `clients.ClientFactory` falls back to `CustomHTTPClient` (`execution/clients/custom.py`) for any
  custom id. The client injects auth **server-side** from `credentials.require()` — the model/agent
  never sees a secret value (same posture as D-25).
- `scoped_read(ctx, "<id>", path)` consults the record's `read_paths` exactly like the built-in
  `READ_SCOPES` — GET-only, no host escape, fail-closed when the list is empty.
- A built-in generic tool `custom_read` (CATEGORY=read) gives immediate chat access to any
  configured custom integration within its read allowlist. Dedicated, nicer-shaped tools are
  drafted by the AI in the **Build** tab and reach `skills/` only via human promote (I-5).

## Owner workflow

1. Integrations → **Add integration** → name it, pick API-key or login template, name the fields,
   set base URL + auth placement + readable path prefixes. Save (admin-gated, audited).
2. Open the new card → **Manage keys** → paste the real credentials (stored 0600, fingerprints only).
3. **Test** → generic probe: GET `probe_path` with auth applied; reports ok/latency.
4. Optional: **Docs → KB** — paste the vendor's docs URL(s); the server fetches (https-only,
   private-address blocked, size-capped), strips to text, and saves under `vault/kb/integrations/`.
   `kb_search` then makes them available to the agent and to the Build draft prompt.
5. **Build capabilities** — jumps to the Build tab pre-filled with the integration context; the AI
   drafts read-only candidate tools into `skills_candidate/`; the owner promotes (I-5 unchanged).

## API

- `POST /api/integrations/custom` — create   ·   `POST /api/integrations/custom/<id>` — update
- `POST /api/integrations/custom/<id>/rename` — change id (migrates stored secrets + read scopes)
- `DELETE /api/integrations/custom/<id>` — delete (clears that integration's secrets)
- `POST /api/integrations/custom/<id>/docs` — fetch a docs URL into the KB
All admin-gated; every mutation audited (`action=config_change`, never values).

## Security notes / lessons

- The record is config, not code → I-4 kill behavior: deleting/disabling the record makes
  `require()` unknown → ClientFactory fail-closed.
- Docs ingestion is the only server-side fetch of an owner-supplied URL: https only, redirects
  capped, RFC-1918/loopback/link-local blocked after DNS resolution, 2 MB cap.
- Renaming an id moves the secret values inside the SecretStore server-side; values are never
  echoed to the browser.
