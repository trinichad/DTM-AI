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

## Auth model — per-client OAuth APP, authorization-code flow (the owner's choice, revised)

**There is no shared/master OAuth app.** Unlike M365 — where Microsoft provides a built-in
multi-tenant public client so no app registration is needed — Google requires an OAuth app to exist
in *some* Google Cloud project, and there is no built-in one. Rather than the MSP owning one shared
app that every client consents to (which, for external clients with admin scopes, drags in Google's
app-verification / security-assessment), **each managed client registers their OWN OAuth app inside
their own Google Cloud** and its `client_id`/`client_secret` are stored **per client**. Every client
is fully independent — separate app, separate consent, separate token — and because each app is
"Internal" to that client's org, it skips Google verification entirely.

**Why authorization-code, not device-code:** Google's device-code ("limited-input device") flow does
**not** permit Admin SDK / Directory scopes. So the sign-in is the **authorization-code flow**: the
admin is sent to a Google consent URL and Google redirects back to our callback with a `code` we
exchange for tokens — still per-client, one consent, a stored per-client refresh token.

**What is global vs per-client:**
- **Global (one setting):** `GWS_REDIRECT_URI` — OUR dashboard callback
  (`https://<dashboard-host>/api/gws/oauth/callback`). Every client's app lists this one URL as an
  authorized redirect. `GWS_SCOPES` optionally overrides the requested scopes.
- **Per client (on that client's card, stored in CredVault entry `gws_app`):** that client's own
  `client_id` + `client_secret`, entered once, then Sign in.

**Per-client setup (what each client's admin does once):** in *their* Google Cloud — create a project,
enable the APIs (Admin SDK / Drive / Licensing / Data Transfer), configure the OAuth consent screen
(**Internal** — no verification needed), create an **OAuth client ID of type "Web application"**, add
our `GWS_REDIRECT_URI` as an authorized redirect, and hand the MSP the client id + secret. The MSP
pastes them into that client's card and clicks **Sign in**; the super-admin consents; the token is
saved under that client. (`hd`/`login_hint` can pre-scope the consent to the client's domain.)

**Storage** (`core/gws_auth.py`): the per-client app secret lives in the client's CredVault entry
`gws_app` (`set_app_credentials`/`get_app_credentials`), with the non-secret client_id + a
configured flag in the plain sidecar `gws_app.json` (0600) — same locked-vault inline fallback as the
tokens. App creds are stored *separately* from the tokens (`gws.json`) so disconnecting a client
keeps its app on file. Web: `POST /api/integrations/gws/app` sets them; `DELETE
/api/integrations/gws/app/{tenant}` clears them.

**Config keys** (`CredentialSpec("gws")` — global only):
| Key | Role |
|---|---|
| `GWS_REDIRECT_URI` | the dashboard callback URL; every client's app registers it (required to connect) |
| `GWS_SCOPES` | optional override of the requested OAuth scopes (defaults below) |

(The `client_id`/`client_secret` are **not** global config — they are per-client in the CredVault.)

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
- **Phase 4 (done):** Shared Drives — gws_list_shared_drives (read), gws_create_shared_drive,
  gws_add_shared_drive_member (Drive API, useDomainAdminAccess). Composites — gws_onboard_user
  (create-if-missing → license → org unit → groups → shared drives) and gws_offboard_user (suspend →
  reset password → remove from all groups → remove license → optional Drive/Docs ownership transfer
  to a manager via the Admin Data Transfer API, app id 55656082996). DEFAULT_SCOPES widened with
  `drive` + `admin.datatransfer`; scopes.py allowlists extended.

## Limitation: Gmail per-user settings need domain-wide delegation, not per-client OAuth

Setting **another** user's Gmail (forwarding, vacation responder, delegation, send-as) requires
impersonating that user, which 3-legged per-client OAuth cannot do — the admin's delegated token
only reaches the admin's OWN mailbox. Those actions need a service account with domain-wide
delegation (the auth model the owner declined). So `gws_offboard_user` transfers Drive/Docs and
suspends (which stops mail access) but does NOT set an auto-forward/vacation reply on the departing
mailbox. If that becomes a requirement, add a DWD service-account path alongside the OAuth one.

- **Phase 5 (done):** Devices + audit. gws_list_mobile_devices, gws_list_chromeos_devices (read),
  gws_wipe_mobile_device (write — account_wipe default / full_wipe / block); gws_audit_log (read —
  login/admin/drive/token/… activity via the Reports API). Scopes added:
  `admin.directory.device.mobile`, `admin.directory.device.chromeos.readonly`,
  `admin.reports.audit.readonly`. Allowlists: `/admin/reports/v1` (read), and device actions bounded
  to `/admin/directory/v1/customer/my_customer/devices` (write) so a device action can't open
  arbitrary customer-scoped writes.

## Not yet built (future phase)
Google **Vault** — eDiscovery matters, holds, exports/downloads (vault.googleapis.com, ediscovery
scopes). It's a multi-step sub-system on its own host (needs a `/v1/matters` host-routing entry that
doesn't collide with Cloud Identity's `/v1/`); deferred rather than half-built. Add the host route +
scope + allowlist + skills when it's needed.

New tools land `CATEGORY=read`, `ENABLED_BY_DEFAULT=False` (I-5); writes default `REQUIRES_APPROVAL`.
