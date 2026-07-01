# SOP — Google Workspace admin integration, per-client OAuth (D-118)

> A-layer SOP. Golden rule (I-7): this is written before the code. Mirrors the Microsoft 365
> integration (`architecture/m365-graph.md`) piece-for-piece so the same guardrails, per-client
> isolation, and Capability-Console gating apply unchanged.

## What this is

A Google Workspace (Google Admin) integration that gives the chat + agent loop the same kind of
admin reach it has for Microsoft 365 — users, groups, org units, licenses, Shared Drives, Gmail
per-user settings, devices, reports — **per managed client**, read-only first, every write behind
the owner's approval gate.

```
ctx.client("gws", tenant) ─ ClientFactory ─ build_gws(cfg, tenant)  [fail-closed if not signed in]
      └── GoogleClient(get/post/put/patch/delete)  → *.googleapis.com
             └── gws_auth.token_source(cfg, tenant)   # fresh access token, auto-refreshed
                   └── POST https://oauth2.googleapis.com/token  (refresh_token grant)
scopes.READ_SCOPES["gws"] / WRITE_SCOPES / DELETE_SCOPES  bound the reachable API surface.
```

## Auth model — per-client OAuth, authorization-code flow (the owner's choice)

Like M365, ONE OAuth app (the owner's Google Cloud project) is shared config; **each managed
client's super-admin signs in separately and consents**, and that client's refresh token is stored
**under that client** — one client's Google access never bleeds into another's.

**Why authorization-code, not device-code:** M365 uses Microsoft's device-code flow, but Google's
device-code ("limited-input device") flow does **not** permit Admin SDK / Directory scopes — those
are restricted to the standard redirect-based flows. So per-client OAuth here is the
**authorization-code flow**: the admin is sent to a Google consent URL, and Google redirects back
to our callback with a `code` we exchange for tokens. Still per-client, still one consent, still a
stored per-client refresh token — just redirect-based instead of a typed user-code.

**One-time owner setup (documented for the Integrations card):**
1. In Google Cloud Console, create/choose a project → enable the APIs you'll use (Admin SDK, and
   later Drive, Gmail, Enterprise License Manager, Cloud Identity, Reports, Vault, Data Transfer).
2. Configure the OAuth consent screen (Internal or, for MSP multi-tenant, External) and create an
   **OAuth client ID of type "Web application"**.
3. Add the dashboard callback as an **Authorized redirect URI**, e.g.
   `https://<your-dashboard-host>/api/gws/oauth/callback`.
4. Enter `GWS_CLIENT_ID`, `GWS_CLIENT_SECRET`, and `GWS_REDIRECT_URI` on the Google Workspace card.

**Per-client connect:** pick a client → "Connect Google Workspace" → the client's super-admin opens
the returned Google URL, consents → Google redirects to the callback → tokens saved under that
client. (`hd`/`login_hint` can pre-scope the consent to the client's domain.)

**Config keys** (`CredentialSpec("gws")`):
| Key | Role |
|---|---|
| `GWS_CLIENT_ID` | the owner's Google OAuth client id (required to connect) |
| `GWS_CLIENT_SECRET` | the OAuth client secret (required — Web-app clients are confidential) |
| `GWS_REDIRECT_URI` | the callback URL, must exactly match the Cloud Console entry |
| `GWS_SCOPES` | optional override of the requested OAuth scopes (defaults below) |

## Token store (mirrors M365, D-37 split)

`execution/core/gws_auth.py`. Secrets (`refresh_token`, `access_token`) live in the client's
encrypted CredVault entry `gws_oauth`; non-secret status/health lives in the plain sidecar
`<vault>/clients/<tenant>/gws.json` (0600) — `{tenant_id? , connected, refresh_fp, access_expires,
obtained, last_refresh, admin_email, domain, last_error?}`. Locked-vault fallback keeps secrets
inline and migrates on first unlocked use — same posture as M365.

**Google specifics vs M365:**
- Google **access tokens are opaque** (not JWTs), so expiry can't be read from the token — we store
  `access_expires = now + expires_in` at save time and refresh on that clock (`_SKEW_S` slack).
- The refresh-token grant returns a new access token but usually **no new refresh token**; we keep
  the existing one (and adopt a rotated one if Google ever sends it). `access_type=offline` +
  `prompt=consent` on the auth URL guarantee a refresh token is issued.
- The exchange response's `id_token` carries the admin's `email` + `hd` (hosted domain) — captured
  into the sidecar (`admin_email`, `domain`) for display, non-secret.

Fail-closed (Rule #8): a client not signed in builds no client; a locked vault raises "unlock the
vault", never a degraded call.

## Client (`execution/clients/google.py`)

`GoogleClient.get/post/put/patch/delete` over `*.googleapis.com`. Google spans several API hosts, so
the client routes by the path's leading segment (`/admin/…` → admin.googleapis.com, `/drive/…` →
www.googleapis.com, `/gmail/…` → gmail.googleapis.com, `/apps/licensing/…` →
licensing.googleapis.com, …). Path guard: must start with `/`, no scheme, no `//`, no `..` — the
host is chosen from a fixed map, never from the path, so a path can't escape to another host. Writes
are only reachable via `scopes.scoped_write`/`scoped_delete` from an owner-approved CATEGORY=write
tool. `probe()` = GET `/admin/directory/v1/customers/my_customer` (cheap, proves the sign-in +
admin rights).

## Scope allowlist (`execution/clients/scopes.py`)

`READ_SCOPES["gws"]` / `WRITE_SCOPES["gws"]` / `DELETE_SCOPES["gws"]` — the trusted primitive
boundary (D-15). Phase 1 allowlists Directory **read** paths only; each later API is opened by adding
its prefix here (reviewed config), never AI-improvised.

## OAuth scopes requested (default `GWS_SCOPES`)

Read-first. Phase 1 needs only the Directory read scope; write/other scopes are added as their tool
phases land (re-consent required when scopes change — same as M365's re-sign-in rule):
`openid email https://www.googleapis.com/auth/admin.directory.user.readonly
https://www.googleapis.com/auth/admin.directory.group.readonly`.

## Capability roadmap (phased, read-only first per the constitution)

- **Phase 1 (this commit):** SOP, `CredentialSpec("gws")`, `gws_auth` (auth-code OAuth + per-client
  token store + refresh), `GoogleClient` + `build_gws`, Directory read allowlist, ClientFactory +
  probe wiring, first read skill `gws_list_users`, unit tests. No live connect yet (see Phase 2).
- **Phase 2 (done):** web endpoints `POST /api/integrations/gws/oauth/start`,
  `GET /api/gws/oauth/callback` (HTML redirect target — validated by the unguessable `state`),
  `GET /api/integrations/gws/clients`, `POST /api/integrations/gws/renew`,
  `DELETE /api/integrations/gws/clients/{tenant}`; dashboard card `gwsSignin` (popup consent +
  postMessage/poll for success). Read skills: gws_list_groups, gws_group_members,
  gws_list_org_units, gws_user_details.
- **Phase 3 (done):** writes (CATEGORY=write, ENABLED_BY_DEFAULT=False, REQUIRES_APPROVAL=True) —
  gws_create_user, gws_suspend_user, gws_restore_user, gws_reset_password, gws_move_org_unit,
  gws_create_group, gws_delete_group, gws_add_group_member, gws_remove_group_member,
  gws_assign_license, gws_remove_license. WRITE_SCOPES/DELETE_SCOPES["gws"] added (POST/PATCH +
  bounded DELETE; a permanent USER delete is intentionally NOT reachable — offboarding suspends).
  DEFAULT_SCOPES widened to the read+write directory/licensing set: the scope is the *reachable*
  surface the admin consents to, NOT the autonomy grant — every write is still Capability-Console +
  approval gated (scope is necessary, not sufficient). A client connected under Phase-1/2 scopes must
  reconnect to grant the write scopes. Owners wanting a strictly read-only grant override GWS_SCOPES
  with the *.readonly variants. Batch (many users) is via the universal `bulk` tool, not per-skill.
- **Phase 4:** onboard/offboard composites (incl. Drive-ownership transfer via the Data Transfer
  API), Gmail per-user settings (forwarding/delegation/send-as — via the admin's consent),
  Shared Drive create + membership, devices, Reports/Vault.

New tools land `CATEGORY=read`, `ENABLED_BY_DEFAULT=False` (I-5); writes default `REQUIRES_APPROVAL`.
