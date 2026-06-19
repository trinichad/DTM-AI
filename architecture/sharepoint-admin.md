# SOP — SharePoint Online admin (site-collection administrator, D-89)

> Granting a current employee access to a **former employee's OneDrive** by making them a
> **site-collection administrator** of that personal site. This is the API equivalent of
> `Set-SPOUser -Site <oneDriveUrl> -LoginName <user> -IsSiteCollectionAdmin $true` — exactly the
> SharePoint Admin Center procedure, done headlessly.

## Why this is a THIRD M365 sign-in (not Graph, not Exchange)
"Site-collection administrator" is a **SharePoint** concept. Microsoft Graph has no API for it —
Graph can grant per-item drive *permissions*, but not site-collection admin. The supported
programmatic channel is **SharePoint CSOM** (`ProcessQuery`) against the tenant admin endpoint,
which needs a token whose **audience is the SharePoint admin host** (`https://<tenant>-admin.
sharepoint.com`). That audience is issued to Microsoft's first-party **SharePoint Online
Management Shell** public client (`9bc3ab49-b65d-410a-85ad-de819febfddc`) — the same app
`Connect-SPOService` uses. So, like `exo` is a second sign-in beside Graph (D-41), `spo` is a
third: one device-code sign-in per client, with a SharePoint admin.

## The per-tenant audience wrinkle
Graph and Exchange have FIXED resource audiences (`graph.microsoft.com`,
`outlook.office365.com`). SharePoint's is **per tenant** (`<tenant>-admin.sharepoint.com`), so the
device-code scope can't be a constant. Because Graph is already connected for the client, we
discover the tenant's SharePoint hostnames from Graph at sign-in time:

- `GET /sites/root?$select=webUrl` → `https://<tenant>.sharepoint.com`
- root host → admin host `<tenant>-admin.sharepoint.com`, my host `<tenant>-my.sharepoint.com`

These three hosts are persisted in the spo sidecar (`vault/clients/<t>/spo.json`) at sign-in, and
the token-refresh scope (`https://<admin-host>/.default offline_access openid profile`) is rebuilt
from `admin_host` on every refresh. **Graph must be connected first** — `start_device_auth(spo)`
fails closed with a clear message otherwise.

## How it works
- `core/m365_auth.py`: a third entry in `_SERVICES` (`spo`). `sharepoint_hosts(cfg, tenant)`
  discovers the hosts via the client's existing Graph token. `start_device_auth` computes the
  per-tenant SharePoint scope and stashes the hosts in the flow; `poll_device_auth` persists them
  into the sidecar on success. `ensure_fresh` rebuilds the SharePoint scope from the stored
  `admin_host` (a missing host = "reconnect SharePoint"). Same encrypted per-client token store as
  Graph/EXO (D-37): the refresh/access tokens live in the CredVault entry `spo_oauth`.
- `clients/spo.py` (`SPOClient`): a CSOM client with a **hard one-method allowlist** —
  `set_site_admin(site_url, login_name, is_admin)` only (mirrors the EXO cmdlet allowlist, D-41).
  It POSTs the `SetSiteAdmin` `ProcessQuery` XML to
  `https://<admin-host>/_vti_bin/client.svc/ProcessQuery`. CSOM is transactional: the response is a
  JSON array whose element carries `ErrorInfo` (non-null ⇒ failure) — so a clean response is a
  positive confirmation, not a fire-and-forget. `probe()` does a tiny authenticated read
  (`GET /_api/web?$select=Title`) to prove the admin-host token works.
- `skills/m365_grant_onedrive_access.py` (`CATEGORY=write`, `RISK_LEVEL=high`,
  `REQUIRES_APPROVAL=True`, `ENABLED_BY_DEFAULT=False`): resolves both users via Graph, finds the
  former employee's OneDrive **site** URL from `GET /users/<id>/drive/root?$select=webUrl,
  sharepointIds` — preferring `sharepointIds.siteUrl` (the canonical site URL) and only falling
  back to stripping the trailing `/Documents` off the library `webUrl`. (Per MS docs the personal-
  site path is NOT safely constructable — numbers/GUIDs can be appended — so the live value is read,
  never string-built.) Then calls `set_site_admin(siteUrl, i:0#.f|membership|<grantee-upn>, true)`
  and returns the OneDrive URL to hand to the grantee.
- Endpoints: the existing `POST /api/integrations/m365/oauth/{start,poll}` and the disconnect /
  clients-status routes are `service`-parameterized — `spo` rides them (admin-only, audited).

## Setup (owner)
1. Connect the client's **Graph** sign-in first (the SharePoint host discovery rides it).
2. On the **Microsoft 365** card, the client now shows a **SharePoint** row → **Sign in** with a
   SharePoint/Global admin (password + MFA). The row flips to connected; **Test** proves it.
3. In the Capability Console, enable `m365_grant_onedrive_access` (and `allow_write` for m365) —
   it is a write tool, so it stays approval-gated.

## login-name format
A member user's site-admin login claim is `i:0#.f|membership|<userPrincipalName>`. The tool builds
this from the grantee's UPN.

## Documentation verification (D-89 research pass, 2026-06-19)
Confirmed against primary Microsoft Learn sources:
- **`Tenant.SetSiteAdmin(string siteUrl, string loginName, bool isSiteAdmin)`** on
  `Microsoft.Online.SharePoint.TenantAdministration.Tenant` — the CSOM op behind
  `Set-SPOUser -IsSiteCollectionAdmin $true`. (MS Learn dn140313; Set-SPOUser ref.) NOTE the third
  param is `isSiteAdmin` (we pass it positionally, so the wire is unaffected).
- **ProcessQuery** is `/_vti_bin/client.svc/ProcessQuery` appended to the site URL, XML request →
  JSON response (a CSOM error structure on an unhandled exception). (MS-CSOM spec.) The exact JSON
  field `ErrorInfo` wasn't on that page but is the well-known CSOM error field.
- **Tenant-admin host** `https://<tenant>-admin.sharepoint.com` is required for admin ops.
  (Connect-SPOService docs.)
- **Graph `GET /users/{id}/drive`** is the documented way to reach a user's OneDrive, and is
  **delegated-only (application permission "Not supported")** — which matches our delegated `spo`
  sign-in. The personal-site URL form `https://<tenant>-my.sharepoint.com/personal/<UPN-underscored>`
  is documented, with the caveat that GUIDs may be appended → read it live (we use
  `sharepointIds.siteUrl`).

Not independently confirmed in that pass (rate-limited; treat as *verify on first live call*, not as
wrong — the `probe()` on the SharePoint row is the cheap live check):
- the Tenant CSOM Constructor `TypeId` GUID `{268004ae-…}`;
- that bearer-token ProcessQuery needs no `X-RequestDigest` (true in practice for OAuth);
- the `https://<tenant>-admin.sharepoint.com/.default` token audience specifically;
- the **SharePoint Online Management Shell** app id `9bc3ab49-…` supporting device-code with admin
  delegated perms (the highest-value thing to confirm on first sign-in);
- the `i:0#.f|membership|<UPN>` login-claim format (well-established; forum/blog corroboration only).

## Gotchas / lessons (self-annealing — append as we learn)
- **Graph not connected ⇒ no SharePoint sign-in.** Host discovery needs the Graph token; the sign-in
  refuses early rather than guessing the hostname from the UPN domain (vanity domains lie).
- **Deleted former-employee account.** If `GET /users/<upn>/drive` 404s, the OneDrive may be
  unprovisioned or the account deleted (its OneDrive then sits under a retention hold). The tool
  reports that instead of inventing a URL — recovery is a Global-admin retention task.
- **CSOM audience is host-specific.** The admin-host token is only valid against `*-admin.
  sharepoint.com`; do not reuse it for `-my` reads. Verification therefore relies on the
  transactional `ErrorInfo` contract of `SetSiteAdmin`, not a second-host re-read.
- **No arbitrary CSOM.** `SPOClient` exposes exactly one mutating method; widening it is a
  deliberate, hand-reviewed code change (same rule as `scopes.*` allowlists), never AI-improvised.
- **Unlicensed OneDrive access is time-boxed, not indefinite (owner guidance).** A former
  employee's OneDrive does NOT require an active license for immediate handoff — the grant works
  either way — but availability degrades on a retention clock: unlicensed < 60 days normally
  accessible; 60–92 days may go read-only; 93+ days may be archived/inaccessible until
  reactivated/relicensed; and if billing is disabled or payment lapses the data may eventually be
  deleted. So the tool reads the former account's `accountEnabled` + `assignedLicenses` and returns
  an `availability` block (with the retention timeline) — unlicensed ⇒ `time_boxed: true` and a
  "copy the files to a durable location promptly" warning folded into the note. Never present this
  access as permanent. These thresholds match Microsoft's *Unlicensed OneDrive accounts* page
  (read-only ~day 60, archived ~day 93) — distinct from the *deleted-user* OneDrive retention window
  (default 30 days, configurable via `Set-SPOTenant -OrphanedPersonalSitesRetentionPeriod`). The
  D-89 research pass was rate-limited on this angle and could not independently re-confirm the exact
  numbers, so **re-read `learn.microsoft.com/sharepoint/unlicensed-onedrive-accounts` +
  `retention-and-deletion` at implementation time — Microsoft revises these periodically.**
