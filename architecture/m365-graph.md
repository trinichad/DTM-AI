# SOP — Microsoft 365 / Graph (delegated device-code sign-in, D-32)

> The honest way to "connect to an O365 account" from a headless agent with MFA. Basic auth
> (username/password) for Exchange Online is removed by Microsoft, and the legacy ROPC flow
> fails the moment an account has MFA — so neither can work here. The OAuth 2.0 **device
> authorization grant** (RFC 8628) is the only delegated path that honors MFA: the user signs in
> with their password + MFA in their browser; we only ever hold the resulting token.

## Why not username/password
- Exchange Online Basic Authentication was permanently disabled by Microsoft (2022–2023).
- ROPC (the one password-grant OAuth flow) returns an error on any MFA-enabled account and is
  blocked for federated/personal accounts — there is no "now enter the MFA code" continuation.
- The interactive `Connect-ExchangeOnline` experience is a *desktop* browser OAuth; a backend
  REST service cannot drive it. Device-code is the headless equivalent.

## Setup (owner, once per app)
1. Entra → App registrations → New registration (single or multi-tenant).
2. Authentication → Advanced → **Allow public client flows = Yes** (device code needs this).
3. API permissions → Microsoft Graph → **Delegated** → add e.g. `User.Read.All` (+ admin consent),
   keep `offline_access`.
4. Integrations → **Microsoft 365 (Graph)** card → Manage keys: paste the **Application (client)
   ID** (and a specific tenant GUID if you want to lock it to one client), Save.
5. **Sign in with Microsoft** → the card shows a code → open microsoft.com/devicelogin, enter the
   code, sign in with password + MFA. The card flips to "connected". **Test** verifies the token.

## How it works
- `core/m365_auth.py`: `start_device_auth` POSTs `/devicecode` (the `device_code` stays
  server-side, keyed by a random `flow_id`); the GUI polls `poll_device_auth(flow_id)` which
  exchanges on success and persists `M365_REFRESH_TOKEN` (+ cached `M365_ACCESS_TOKEN`) to the
  SecretStore. `ensure_fresh` re-mints the access token from the refresh token near expiry and
  persists rotations (env/.env would shadow them — tokens MUST live in secrets.local).
- `clients/m365.py` (`M365Client`): read-only Graph client built via the ClientFactory with a
  `token_source` closure, so every call uses a fresh token. Reads are bounded by
  `scopes.READ_SCOPES['m365']` (`/users`, `/groups`, `/organization`, `/subscribedSkus`,
  `/directoryRoles`, `/devices`, `/me`) — widen there deliberately, never AI-improvised.
- Spec `m365` (group `vendor`): required `M365_CLIENT_ID` + `M365_REFRESH_TOKEN`; optional
  `M365_TENANT` (default `organizations`), `M365_SCOPES` (default
  `offline_access openid profile User.Read.All`), `M365_ACCESS_TOKEN` cache. Token keys are
  hidden from the credential form (managed by the sign-in flow).
- Endpoints: `POST /api/integrations/m365/oauth/{start,poll}` (admin-only, audited).

## Capabilities
- `skills/m365_list_users.py` — `m365_list_users` (read): Graph `/users` with `$select`/`$top`/
  optional `$filter`. Returns id, displayName, userPrincipalName, mail, accountEnabled, jobTitle,
  department. Enabled by default; fails closed with a clear message until signed in.
- Build more (mailboxes, MFA status via authentication methods, licenses) from the M365 KB docs;
  add the Graph path to `READ_SCOPES['m365']` first, then draft the tool in the Build tab.

## Security
- The password + MFA are entered at Microsoft; MSP AI never receives them. Only tokens are stored
  (0600 SecretStore, fingerprint-only in the UI). Disconnect clears both tokens. Cloud gating and
  every dispatch guardrail apply unchanged; `m365_list_users` is read-only and audited.

---

## Amendment (2026-06-10, D-33) — PER-CLIENT at MSP scale

The owner runs M365 across many client tenants with a **separate admin per client** (no Partner
Center/GDAP), and chose to keep delegated device-code. So M365 is now **per managed client**, not one
global token:

- **One multi-tenant public-client app** (its `M365_CLIENT_ID` + `M365_SCOPES` are global config,
  set once on the card). Register it multi-tenant with "Allow public client flows = Yes" and the
  delegated Graph permissions you want (e.g. `User.Read.All`).
- **Each managed client signs in separately** with that client's own admin (password + MFA at
  microsoft.com/devicelogin). Tokens are stored **per client** at
  `vault/clients/<tenant>/m365.json` (0600; same at-rest posture as secrets.local) — one client's
  Graph access never bleeds into another's. The `tid` claim is captured from the token so refresh
  hits the right tenant.
- **The card** (Integrations → M365 → Manage keys) sets the app id once, then lists every
  registered client with Sign in / Disconnect + a connection fingerprint. `spec.required` is just
  `M365_CLIENT_ID` (the app); per-client connection is shown in the panel, not the spec.
- **The Graph client is built per (m365, tenant)** in the ClientFactory and fails closed if the
  bound client isn't signed in (or the session is "*"). So `m365_list_users` automatically reads
  **the currently-selected client's** tenant — pick a client, then ask.
- Endpoints (admin, audited): `POST /api/integrations/m365/oauth/start {tenant}` ·
  `POST …/oauth/poll {flow_id}` · `GET …/m365/clients` · `DELETE …/m365/clients/<tenant>`.

Operational note: a client's token is re-minted automatically from its refresh token, but if that
breaks (password/MFA/Conditional-Access change, ~90 days idle) that one client shows "not
connected" and re-signs-in — independently of every other client.

---

## Amendment (2026-06-10, D-34) — no app registration required

The owner doesn't want to register an app at all. Every OAuth token is issued to *an* app, but it
needn't be the owner's: M365 now defaults to Microsoft's own first-party public client
**"Microsoft Graph Command Line Tools"** (`14d82eec-204b-4c2f-b7e8-296a70dab67e`) — the same app
`Connect-MgGraph` uses. It exists in every tenant and supports device-code + delegated Graph
scopes, so each client admin just signs in (consenting to the requested scopes once) and the token
caches per-client. `M365_CLIENT_ID` is now OPTIONAL — set it only to override with your own app.

So the whole flow for the owner: open the M365 card → per-client list → **Sign in** → that client's
admin completes password + MFA at microsoft.com/devicelogin → token cached at
`vault/clients/<tenant>/m365.json`. Zero Azure portal work.

Caveats (honest): the consent prompt the admin sees names "Microsoft Graph Command Line Tools", not
"MSP AI". A tenant with strict app-governance or device-code Conditional-Access blocks would need
the owner's own registered app (set `M365_CLIENT_ID`). Admin-restricted scopes (e.g. User.Read.All)
still require the signing admin to consent the first time.

---

## Amendment (2026-06-10, D-35) — searchable users + token health + auto-renew

**Searchable users.** `m365_list_users` gains a `search` param (Graph `$search` across
displayName/mail/UPN/given/surname, `ConsistencyLevel: eventual` + `$count`). For 200+ user
tenants the agent searches by name/email instead of dumping the list, and the result reports
`total_in_tenant` + a "narrow with search" note when a page is partial. `filter` (OData) and `top`
(default 200, max 999) remain.

**Token health.** Each per-client `m365.json` now records `obtained`, `last_refresh`, and (on a
failed refresh) `last_error`/`last_error_at`. `health(cfg, tenant)` returns connection state, the
access-token expiry, `refresh_valid_until` (last_refresh + 90d), and a `healthy` flag.
`GET /api/integrations/m365/clients` includes this, and the card shows per-client status: "renewed
Xh ago · auto-renews (good ~Nd)", an amber warning under ~14 days, or "re-sign-in needed" (with the
error) when a refresh has failed (password/MFA/Conditional-Access change, revocation).

**Auto-renew (keep-alive).** A daemon thread (`_start_m365_renewer`, started in `create_server`)
calls `renew_all` every `MSPAI_M365_RENEW_HOURS` hours (default 12; 0 disables) — forcing a real
refresh-grant per connected client so the 90-day idle clock never elapses, even for clients the
agent hasn't queried. Failures are recorded per-client (surfaced as "re-sign-in needed"), never
crash the server. `renew(cfg, tenant)` / `renew_all(cfg)` are also exposed via
`POST /api/integrations/m365/renew {tenant?}` and a "Renew all" button on the card.

---

## Amendment (2026-06-11, D-37) — tokens live in the client's CredVault entry

The owner asked for the M365 tokens to live in each client's credentials file (Memory →
Credentials), not a plaintext JSON. Storage is now **split**:

- **Secrets** (`refresh_token` + cached `access_token`) are stored in the client's encrypted
  `credentials.enc` via the shared `CredVault`, under the system-managed label **`m365_oauth`**
  (`get_credvault()` — one process-wide instance, so the DEK an admin unlocks via the web API is
  the same one `m365_auth` uses). The entry shows up in the Memory tab's credential list as
  fingerprints, like every other secret, and rotates on every refresh (`updated_by: system:m365`).
- **Non-secret status/health** stays in the plain per-client sidecar `vault/clients/<tenant>/m365.json`
  (0600): `tenant_id`, `connected`, `refresh_fp`, `access_expires`, `obtained`, `last_refresh`,
  `last_error`/`last_error_at`. So `is_connected` / `list_connected` / `health` / `fingerprint_for`
  (the card, the renewer's worklist) never need a vault decrypt.

**Locked-vault behavior (honest, fail-closed):**
- A Graph call (`ensure_fresh`) on a migrated client with the vault locked raises a clear
  "credential vault is locked — unlock it (or enable agent auto-unlock)" error; it is NOT recorded
  as a token failure, so the card doesn't lie with "re-sign-in needed". Unattended operation
  (the 12 h renewer, post-reboot Graph calls) therefore wants the D-30 **agent auto-unlock** toggle
  ON; with it OFF, M365 works only while an admin has the vault unlocked (TTL default 8 h).
- `renew_all` reports those clients in a separate `locked` list (never `failed`).
- **Disconnect requires the vault open** when the secrets are vault-held: deleting the connection
  must actually delete the token, so `clear_tokens` raises and the API answers 409 "unlock the
  credential vault" rather than leaving the secret behind.
- **Sign-in / token rotation while the vault is locked** falls back to the pre-D-37 inline sidecar
  (same 0600 posture as before) so a refresh rotation is never lost; the secrets **migrate into the
  vault automatically on the first use after an unlock** (this also migrates every pre-D-37
  `m365.json` in place — no manual step).
- Deleting the `m365_oauth` entry in the credentials manager effectively disconnects that client:
  the sidecar self-heals to "not connected" on the next token use.

---

## Amendment (2026-06-11, D-40) — gated Graph WRITES (build-on-request)

`M365Client` now has `post(path, body)` / `patch(path, body)` alongside `get` — reachable only
through `scoped_write` (`clients/scopes.py`, `WRITE_SCOPES["m365"] = ("/users",)`) from a
CATEGORY=write tool that the owner has promoted, enabled, opened `allow_write` for, and approved
per-run (see self-development.md D-40). The READ posture of everything that exists today is
unchanged.

**Consent caveat:** the default delegated scopes are read-only (`User.Read.All`). For a write tool
(create user, set authentication methods) the owner must add write scopes on the M365 card —
`M365_SCOPES` = `offline_access openid profile User.Read.All User.ReadWrite.All
UserAuthenticationMethod.ReadWrite.All` — and the affected client must **sign in again** so the
admin consents to the new scopes. Until then Graph answers 403 and the tool fails closed with the
scope hint.

---

## Amendment (2026-06-11, D-55) — user creation + licensing (hand-written write tools)

- `m365_create_user` (**write**, REQUIRES_APPROVAL=True, disabled by default): POST `/users`.
  Rules learned from the AI-Test misfire:
  - **The User ID (userPrincipalName) IS the requested email address** — never derived from the
    display name. `mailNickname` = the address's local part.
  - **First/last name are required parameters.** The tool DESCRIPTION instructs the agent to ASK
    the owner for them when not given — never invent, never omit. (`""` is accepted only when the
    owner explicitly said "no name".)
  - Optional profile fields, set only when provided: job_title, department, office, office_phone,
    mobile_phone, street_address, city, state, postal_code, country (Graph: jobTitle, department,
    officeLocation, businessPhones[0], mobilePhone, streetAddress, city, state, postalCode,
    country).
  - The initial password is generated server-side (`secrets` module, never by the LLM), returned
    once in the result with `forceChangePasswordNextSignIn: true`. The owner may instead pass an
    explicit `password` (D-55 follow-up) — used verbatim (Entra enforces its own complexity policy
    and rejects weak ones) and NOT echoed back in the result (the owner already knows it; results
    land in chat history). `must_change` (default true) controls forceChangePasswordNextSignIn.
  - Verifies by re-reading `/users/<upn>` after the POST.
- `m365_list_licenses` (read): GET `/subscribedSkus` → sku part number, total/consumed/available.
  Needs `Organization.Read.All` consented (read scope — add to `M365_SCOPES` + re-sign-in).
  Pass `license` to instead list THAT SKU's **apps** (Graph `servicePlans`: name, plan id,
  provisioning status, applies-to) — the checkable boxes the admin center shows.
- `m365_assign_license` (**write**): resolves the SKU (accepts the part number, e.g.
  `O365_BUSINESS_PREMIUM`, or a GUID) against `/subscribedSkus`, refuses if none available,
  auto-sets `usageLocation` (default `US`, parameterized) when the user lacks one (Graph hard
  requirement), then POST `/users/<upn>/assignLicense`. Verifies via the user's `assignedLicenses`.
  **App check/uncheck (D-55 follow-up):** optional `disabled_apps` = the COMPLETE list of the
  license's apps to leave UNCHECKED (service-plan names from `m365_list_licenses license=...`,
  case-insensitive, or plan GUIDs; `[]` = everything checked). Wire: `disabledPlans` on
  `assignLicense`. Only `appliesTo: User` plans are disableable — Company-level plans are
  refused with the valid choices. If the user ALREADY holds the license, passing `disabled_apps`
  RE-assigns with the new set (that's how Graph edits the checkboxes; no extra seat consumed —
  the seat check is skipped on update); without `disabled_apps` it stays a clean no-op. The
  verify pass compares the user's `disabledPlans` set, and the result names enabled/disabled
  apps so the owner sees exactly what the user got.
- Write-scope consent caveat (D-40) applies to both writes: `User.ReadWrite.All` (+
  `Organization.Read.All`) in `M365_SCOPES`, then re-sign-in the client.

---

## Amendment (2026-06-11, D-56) — auth methods / per-user MFA / Entra groups / SharePoint reads / Autopilot

Scope widening (deliberate, hand-edited): READ_SCOPES m365 += `/sites`, `/deviceManagement`;
WRITE_SCOPES m365 += `/groups`, `/deviceManagement`. Each tool answers 403 with the exact
delegated scope to add to `M365_SCOPES` (then re-sign-in): phone methods =
`UserAuthenticationMethod.ReadWrite.All`; per-user MFA state read = `UserAuthenticationMethod.Read.All`,
write = `Policy.ReadWrite.AuthenticationMethod`; groups = `Group.ReadWrite.All`; sites =
`Sites.Read.All`; Autopilot = `DeviceManagementServiceConfig.ReadWrite.All`.

- `m365_add_phone_auth` (write): a user's phone sign-in method
  (`/users/<upn>/authentication/phoneMethods`). Numbers normalized to Microsoft's
  `+1 5551234567` shape (10-digit / 1+10-digit input assumed NANP; other countries must
  arrive pre-formatted `+CC number`). If a method of that type exists it's UPDATED (PATCH),
  else created; verified by re-reading the methods list.
- `m365_set_mfa` (write): per-user MFA state — PATCH `/users/<upn>/authentication/requirements`
  `perUserMfaState` ∈ enforced|enabled|disabled (the modern API for the old per-user MFA
  portal; "enabled" auto-promotes to enforced once the user registers). Verified by re-read.
- `m365_mfa_status` (read): one user → their `perUserMfaState`; no user → sweep the tenant
  (capped, default 100 / max 500 users — N+1 Graph calls, noted in the result) and bucket
  UPNs into enforced/enabled/disabled.
- `m365_list_groups` (read) + `m365_create_group` (write) + `m365_add_security_group_member`
  (write): directory (Entra) groups, `kind` ∈ security|m365|dynamic. Dynamic groups take a
  `membership_rule` and members can NEVER be added manually (Entra rule-driven — the add tool
  refuses with that explanation). M365-group creation notes it auto-provisions a TEAM SITE
  (that's how team sites are born). Member adds go via `/groups/<id>/members/$ref` and are
  verified against the member list. Groups resolve by id, displayName, or mailNickname.
- `m365_list_sharepoint_sites` (read): `GET /sites?search=` slim list.
  `m365_sharepoint_site_details` (read): one site — storage quota via `/sites/<id>/drive`,
  and the drive **owner.group trick**: a group-connected drive ⇒ TEAM site (members listed
  from the group); no owning group ⇒ communication/standalone document site.
  **Site CREATION is not in Graph v1.0** (communication sites + library settings like
  Require-Check-Out live in the SharePoint REST API = a future per-tenant SP connector, like
  EXO was). Team sites: create an M365 group (`m365_create_group kind=m365`).
- `m365_list_autopilot_devices` (read) + `m365_add_autopilot_device` (write: import a hardware
  hash with serial, group tag, assigned user — POST `importedWindowsAutopilotDeviceIdentities`;
  base64 hash sanity-checked) + `m365_update_autopilot_device` (write: `updateDeviceProperties`
  action for tag/user/name on an existing device). HONESTY RULE: Autopilot imports/updates are
  ASYNC server-side — the tools report "submitted/accepted", never "imported", and say to
  re-check with the list tool in ~15 min.

---

## Amendment (2026-06-11, D-57) — m365_offboard_user (the disable-account composite)

One owner-approved action that runs the standard MSP offboard, each step an OPTIONAL flag
(all default ON; the owner's prompt can turn any off). Order is deliberate — Graph identity
steps run on the user's OBJECT ID (resolved once) so the later SMTP/UPN rename can't strand
them:

1. `block_signin` — PATCH `accountEnabled:false`.
2. `sign_out_devices` — POST `/users/<id>/revokeSignInSessions` (kills refresh tokens; access
   tokens die at natural expiry, ≤1 h).
3. `reset_password` — PATCH `passwordProfile` to a server-generated random password that is
   deliberately NOT returned to anyone (the goal is "nobody knows it").
4. `convert_to_shared` — EXO `Set-Mailbox -Type Shared` (frees the license), verified.
5. `remove_licenses` — checks mailbox size FIRST (`Get-MailboxStatistics`): **> 50 GB → the
   step is SKIPPED with a warning** (a shared mailbox over 50 GB still requires a license —
   removing it would strand the data); otherwise removes every assigned SKU and verifies the
   list is empty.
6. `hide_from_gal` — `Set-Mailbox HiddenFromAddressListsEnabled:true`, verified.
7. `rename_smtp` — primary SMTP + sign-in UPN become `zzz_<local>@<domain>` AND the original
   address is REMOVED from the proxy list, so mail to the old address BOUNCES (this is the
   difference from exo_set_primary_smtp, which keeps the old address as an alias). Verified.
8. `prefix_display_name` — Graph PATCH displayName to `zzz_<name>` (by object id, so it works
   after the UPN rename) — the owner's convention for spotting disabled accounts.

Steps run sequentially and FAILURES DON'T ABORT the rest — every step reports ok/skipped/error
in the result's `steps` map and overall `ok` is true only when every requested step landed.
Tool is CATEGORY=write (everything is reversible), RISK high, per-run approval.

---

## Amendment (2026-06-12, D-60) — a deliberate beta escape hatch (per-user MFA fix)

The per-user MFA API (`/users/<id>/authentication/requirements`, `perUserMfaState`) exists ONLY on
Graph **beta** — on v1.0 it 400s with "Resource not found for the segment 'requirements'" (hit in
production; confirmed against the v1.0 `authentication` resource doc, which has no `requirements`
relationship). Fix:

- A tool opts into beta by prefixing the PATH with `/beta/...`. `M365Client` rewrites the URL to
  `https://graph.microsoft.com/beta/...`; `scopes.is_allowed_read/write` strip the `/beta` prefix
  and validate the REMAINDER against the same allowlist — beta widens the API version, never the
  reachable surface.
- Used ONLY by `m365_set_mfa` + `m365_mfa_status`. Honest caveat carried in-tool: beta APIs are
  unsupported-for-production by Microsoft and can change; when `requirements` GAs to v1.0, drop
  the prefix.
- Corrected scope hints (docs audit): READING per-user MFA needs **Policy.Read.All** (not
  UserAuthenticationMethod.Read.All); writing stays Policy.ReadWrite.AuthenticationMethod. The
  offboard's password-reset 403 now hints **User-PasswordProfile.ReadWrite.All** + User
  Administrator role explicitly.

**D-60 follow-up — `m365_list_auth_methods` (read).** "MFA is enforced, but WITH WHAT?" —
GET `/users/<upn>/authentication/methods` (stable v1.0). Each method's `@odata.type` maps to a
friendly name (Microsoft Authenticator / phone SMS-call + number / FIDO2 key / Windows Hello /
TOTP app / Temporary Access Pass / email / password) with its useful detail (device name, phone
number, key model). Methods are split into `mfa_methods` vs `other_methods` — password and the
SSPR-only email method are NOT MFA and must never be reported as such. Scope:
UserAuthenticationMethod.Read.All (covered by the ReadWrite.All already in the recommended
M365_SCOPES). Pairs with m365_mfa_status (state) — together they answer "is MFA on, and how".

---

## Amendment (2026-06-12, D-65) — Graph DELETE + remove counterparts

`M365Client` gains `delete(path)`; `scopes` gains DELETE_SCOPES['m365'] + scoped_delete (allow-
listed prefixes /groups, /users — same host-escape guards). New remove skills mirror the adds:
- m365_remove_security_group_member (DELETE /groups/{id}/members/{uid}/$ref)
- m365_delete_group (DELETE /groups/{id}; M365-group deletion cascades to its site + group mailbox
  — soft-deleted ~30 days; WRITE high-risk with a loud warning, not destructive since recoverable)
- m365_remove_license (assignLicense removeLicenses:[sku] — the inverse of assign)
- m365_remove_phone_auth (DELETE /users/{id}/authentication/phoneMethods/{methodId})
- m365_remove_autopilot_device (DELETE /deviceManagement/windowsAutopilotDeviceIdentities/{id})
Delete scope consent: Group.ReadWrite.All / UserAuthenticationMethod.ReadWrite.All /
DeviceManagementServiceConfig.ReadWrite.All already in the recommended M365_SCOPES.

---

## Amendment (2026-06-12, D-66) — docs-audit fix
- `m365_mfa_status`: the whole-client sweep lists /users (needs **User.Read.All**) AND reads each
  /authentication/requirements (needs **Policy.Read.All**); the 403 hint now names both (was
  Policy.Read.All only, which mis-pointed a failure on the user-list step).
- Confirmed NOT code bugs by the audit: the offboard password-reset 403 is a scope/role grant
  (User-PasswordProfile.ReadWrite.All + User Administrator); the per-user MFA /beta endpoint is
  correct. Everything else (auth methods, phone add/remove, license assign/remove, groups,
  sites, autopilot, DELETE scopes) verified correct against KB + Microsoft Learn.

---

## Amendment (2026-06-12, D-67) — robustness hardening (audit non-blocking notes)
- **Group membership checks** (`m365_add/remove_security_group_member`): replaced the list-all
  (`$top=999`) member scan with a TARGETED `members?$filter=id eq '<uid>'&$count=true` query
  (ConsistencyLevel: eventual is always sent) via `_graph_common.is_group_member` — correct even
  for groups with >999 members (no false-negative verify). Falls back to a paged scan if the
  directory rejects the filter. 403 hints now also name the required group-management role.
- **Autopilot serial lookup** (`m365_update/remove/list_autopilot_devices`): a serial with a
  space/hyphen can 400 the `contains(serialNumber,…)` filter. `_graph_common.find_autopilot_by_serial`
  now skips the server filter for spaced serials and does an exact client-side match over paged
  results (and falls back the same way if the filter errors). The list tool client-filters too.
- **SharePoint site listing** (`m365_list_sharepoint_sites`): now flags `@odata.nextLink`
  truncation ("more sites exist … narrow/raise limit") instead of silently returning a partial set.

---

## Amendment (2026-06-22, D-90) — approval cards show a plain-language preview (resolve ids → names)

Problem: a write proposed with an opaque id (e.g. `m365_add_security_group_member` with
`group: "0615ff24-…"`, which the model gets from `m365_list_entra_groups`) showed the owner only
the raw GUID on the approval card — unconfirmable. You can't sign off on "add pgarcia to
0615ff24-…".

Fix — a general, opt-in hook (cross-cutting, not M365-specific): a tool may export
`describe_approval(ctx, args) -> dict | str | None`. When `dispatch()` defers a write, it calls
this (best-effort, read-only) and stamps the result on the approval row (`args_preview`, new
column, migrated in place) AND on the pending envelope (`approval_preview`). It flows through
`turn.pending["preview"]`, the `approval_required` event, the streamed `answer` frame, the
persisted `meta.pending`, and the `/api/approvals` bell list — so the inline card AND the bell card
render a green "In plain terms — confirm this" block above the raw args. Any failure → preview is
`None` and the card falls back to raw args (Rule #2: this only RESOLVES ids the tool already
understands, it never invents). `m365_add/remove_security_group_member` implement it (group id →
`displayName · id`, plus the user). Registry discovers the hook via `ToolInfo.describe_approval`.
Tests: `test_describe_approval_preview_is_resolved_and_stored`,
`test_pending_turn_carries_the_preview_to_the_card`, `test_preview_failure_falls_back_to_raw_args`.

## Amendment (2026-06-22, D-93) — `m365_list_users` substring search (`name_contains`)

"List the users with `zzz_` in their name" made the agent loop `m365_list_users` to the tool-call
cap with no answer: Graph keyword `$search` is word/prefix-tokenized (it can't match a substring
like a `zzz_` prefix reliably) and Graph has no `contains()` on user properties, so the model had no
deterministic call and kept retrying. Fix: new `name_contains` parameter does a SERVER scan +
CLIENT-SIDE substring filter — follows `@odata.nextLink` paging (`$skiptoken`, capped at
`_MAX_SCAN_PAGES`=30 ≈ 30k users) and returns every user whose `displayName`, `userPrincipalName`,
or `mail` contains the text (case-insensitive), complete in ONE call. Works per-client and across
`*` (tags each match's `tenant`); both annotate a `note` if the page cap was hit. The description
steers the model to `name_contains` for naming conventions (e.g. `zzz_`) and to `search` for
fast word/prefix lookups. Test: `test_name_contains_filters_substring_across_pages` (paging +
mixed-case + name/UPN hits).

## Amendment (2026-06-22, D-94) — large user lists fit context; no truncation loop

"Give me the formatted table with ALL 140 zzz_ users" looped `m365_list_users` to the round cap.
Cause: 140 full user objects (~28 KB) exceeded the agent's 20 KB tool-result budget, so
`tool_payload` truncated the list and (old note) told the model to "re-call with a name_contains
filter" — but the query was already as narrow as intended, so each re-call returned the same page →
loop, no answer. Two fixes:
- **Slim the rows** (`m365_list_users._slim`): drop the GUID `id` and empty fields, omit `mail` when
  it just echoes the UPN. Applied on every return path (list / search / name_contains / all-clients).
  ~140 slimmed users now serialize under the 20 KB cap, so the whole match set reaches the model in
  ONE result and it can emit the full table (dashboard then offers CSV/Excel/PDF export). Downstream
  tools resolve by UPN/email, so dropping the raw id costs nothing. Test:
  `test_large_match_set_fits_model_context_budget`.
- **Loop-safe truncation note** (`agent.tool_payload`, generic): when a list still must be trimmed
  to fit, the `_truncated` note now says the omitted rows are NOT absent, that re-calling with the
  SAME args returns the SAME page (do NOT loop), and to present what fits + state the total + offer
  to narrow or export. Only a NARROWER query returns different rows.

## Amendment (2026-06-23, D-102) — list a user's group memberships (offboarding read)

Live gap during an offboarding ("remove any groups the user was a member of"): the agent could
remove a user from a NAMED group (m365_remove_security_group_member / exo_remove_group_member) but
had no tool to ENUMERATE which groups a user belongs to — so it couldn't act without the owner
naming each group. Added the missing read.

- `m365_user_groups` (read, default-on): `GET /users/{id}/memberOf/microsoft.graph.group` (typed
  cast → only group objects, not the directoryRole entries memberOf otherwise returns), paged
  ($top 999, skiptoken loop, capped). DIRECT memberships by default; `transitive=true` switches to
  `transitiveMemberOf` (groups inherited via nesting — visibility only, not directly removable).
  Each group is `classify()`-tagged (security / m365 / distribution-mail-enabled / dynamic) and
  carries `removable` + `remove_with` (the exact tool to use): distribution/mail-enabled →
  exo_remove_group_member, security/M365 → m365_remove_security_group_member, dynamic → not
  removable (rule-based; a `note` lists them and says to fix via the membership rule/attribute).
  403 names `GroupMember.Read.All`. Path is covered by the existing `/users` read prefix — no
  allowlist change. This is the read half of the offboarding group cleanup; m365_offboard_user does
  NOT touch group membership, so the two compose (list → remove each removable group).

## Amendment (2026-06-23, D-103b) — group-member removal: replication-lag poll + on-prem guard

`m365_remove_security_group_member` reported "the remove returned no error but the user is still in
the member list" during an offboarding. Two causes, both now handled:
- **Eventual consistency.** The DELETE `/groups/{id}/members/{uid}/$ref` succeeds, but the
  (ConsistencyLevel: eventual) membership query right after can STILL list the user for a few
  seconds → false "didn't stick." Now the verify POLLS `is_group_member` (5 × 1.5s) before failing,
  and on timeout returns `ok=false, pending=true` ("Entra accepted it; replication lag — re-check")
  instead of the scary message (same lesson as D-99 mailbox-type conversion).
- **On-prem-synced group.** If `onPremisesSyncEnabled` is true the group's membership is mastered in
  Active Directory (AAD Connect re-syncs it), so a cloud removal won't stick. `resolve_group` now
  selects `onPremisesSyncEnabled` and the tool refuses up front with an actionable message: remove
  the member in on-prem AD, which then syncs to Entra (mirrors the D-91 cloud-management guard).
Tests: lag → success after poll; on-prem → guarded (no DELETE); persistent → pending; non-member → no-op.

## Amendment (2026-06-23, D-104) — license removal verify polls (eventual consistency, again)

`m365_remove_license` reported "the license is still on the user after removal — check the admin
center", but an immediate re-check showed the user had NO licenses: the removal succeeded and the
verify read was stale. `POST /users/{id}/assignLicense` is eventually-consistent — `assignedLicenses`
lags the write by a few seconds. Fixed the same way as D-103b / D-99: the verify now POLLS the
re-read (5 × 1.5s) before failing, and on timeout returns `ok=false, pending=true` ("M365 accepted
it; propagation — re-check with m365_list_user_license_assignments, don't re-run") instead of the
scary message. Tests: lag → success after poll; persistent → pending; not-assigned → no-op.

**Known follow-up:** `m365_offboard_user` still verifies its inline convert-to-shared (via
`set_and_verify`, the pre-D-99 single read) and license-removal steps with a SINGLE immediate read,
so it can emit the same false-negative for those steps. Lower priority (the standalone tools used in
practice are now fixed); align it — ideally via a shared `poll_until(read, predicate)` helper reused
by all four verifies — when the offboard path is next touched.

## Amendment (2026-06-23, D-104b) — audit: ALL Graph write-verifies now poll (shared `settle`)

After D-104 (license removal), swept every `m365_*` write tool for the same "write → single immediate
re-read → false-fail" pattern, since Graph DIRECTORY writes are uniformly eventually-consistent.
Added one shared helper `_graph_common.settle(read, ok)` — checks IMMEDIATELY first (zero latency on
the common already-consistent path), then sleeps + retries while stale; `MSPAI_VERIFY_DELAY` (seconds)
tunes the wait (tests set 0). Applied to every Graph verify that re-reads directory state:
- membership: `m365_add_security_group_member`, `m365_remove_security_group_member`
- licenses: `m365_assign_license`, `m365_remove_license`
- objects: `m365_create_user`, `m365_create_group` (poll until readable), `m365_delete_group`
  (poll until 404/gone)
- auth: `m365_add_phone_auth`, `m365_remove_phone_auth`, `m365_set_mfa`
All now return `pending=true` with a "propagation lag — re-check, don't re-run" message on a genuine
timeout, instead of "check the admin center directly."

**Deliberately NOT changed:**
- **EXO Set-Mailbox tools** (`exo_set_*`, `set_and_verify`) — Exchange admin is read-your-writes; the
  one async exception (mailbox-type conversion) already polls (D-99). DL/permission/alias add-remove
  in EXO are immediate too.
- **`m365_remove_autopilot_device`** — Intune propagation is MINUTES, not seconds; a short poll won't
  help and its message already says "Intune can lag — re-check with m365_list_autopilot_devices."
- **`m365_offboard_user`** inline convert + license steps still single-read (D-104 follow-up) — align
  to `settle`/the EXO poll when that path is next touched.
Tests: `test_remove_security_group_member_polls_through_replication_lag`, `_on_prem_synced_is_guarded`.

## Amendment (2026-06-23, D-104c) — m365_offboard_user inline steps aligned to the poll

Closed the D-104b follow-up: `m365_offboard_user` still verified its inline convert-to-shared and
license-removal steps with a SINGLE immediate read, so a long offboard could emit the same false
"didn't stick" the standalone tools used to. Now both poll:
- **convert step** no longer uses `set_and_verify` (single read). It runs `Set-Mailbox -Type Shared`
  then `_exo_common.settle` on RecipientTypeDetails. Type isn't a directory-mastered attribute, so the
  D-91 cloud-management guard never applied to a convert — nothing lost by bypassing set_and_verify.
- **license step** polls the post-removal `assignedLicenses` read with `_graph_common.settle` until
  empty (was an immediate `left == 0`).
Added a generic `_exo_common.settle(read, ok)` (the EXO twin of `_graph_common.settle`; default
6 × 2s since EXO conversions are slower; `MSPAI_VERIFY_DELAY` tunes it) and retrofitted the standalone
`exo_convert_mailbox` onto it too, so there's ONE poll mechanism per layer. EXO `set_and_verify`
(ordinary read-your-writes attribute sets) is unchanged. Offboard tests updated (the convert step now
does Set + one poll-read instead of set_and_verify's preflight+verify pair).

## Amendment (2026-06-23, D-105) — offboard reorder + hybrid-aware + group listing; rename split out

Owner redesigned the offboarding flow:
- **New step order:** sign_out_devices → reset_password → block_signin → convert_to_shared →
  remove_licenses (50 GB safeguard) → hide_from_gal → prefix_display_name → list_groups.
- **Hybrid-aware:** the offboard resolves the user with `onPremisesSyncEnabled`. When TRUE (Entra
  Connect / hybrid) the password reset and sign-in block are mastered in on-prem AD, so both are
  SKIPPED with guidance ("disable/reset in on-prem AD — it syncs to Entra"), not failed. sign-out (a
  cloud session revoke) still runs.
- **Rename removed from the composite** → new standalone `exo_rename_smtp` (write, high, approval,
  default-off): rename primary+UPN to zzz_<old>, drop the old proxy so mail bounces, verify; carries
  the D-91 cloud-management hint. Run manually once a manager confirms no mail flow is needed.
- **Group cleanup is now LIST-then-ask, never automatic.** Offboard ends by listing the user's
  DISTRIBUTION groups (EXO-authoritative, see exchange-online.md D-105) + SECURITY groups (Graph,
  `m365_user_groups` filtered to kind=security) into `group_cleanup` with a strong `instruction`:
  the agent must ASK the owner which to remove (all/some/none) and then call exo_remove_group_member
  / m365_remove_security_group_member one-per-group (each its own approval — pairs with the D-103
  reject-continues flow). The tool itself removes nothing.
Flags reordered; `rename_smtp` flag dropped; `list_groups` added (default on). Tests: full offboard
(list_groups off), hybrid-skips, lists-groups-without-removing, big-mailbox, flags-off.
