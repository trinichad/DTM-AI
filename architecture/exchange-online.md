# SOP — Exchange Online connector (cmdlet allowlist over the EXO admin REST API, D-41)

> Why this exists: shared mailboxes and mailbox permissions (Full Access / Send As) are NOT
> exposed by Microsoft Graph — they live in Exchange Online. The supported programmatic channel
> is the EXO admin REST API (the same one Exchange Online PowerShell v3 uses under the hood).
> This connector gives the agent a STRICT, allow-listed slice of it, behind every existing gate.

## Auth (per managed client, device-code — same pattern as Graph, D-33)

A Graph token cannot call Exchange — the audience differs and the Graph CLI app has no EXO
permission. So Exchange is a SECOND per-client sign-in, sharing the m365 device-code machinery
(`core/m365_auth.py`, `service="exo"`):

- App: Microsoft's first-party public client **"Microsoft Exchange REST API Based PowerShell"**
  (`fb78d390-0c51-40cd-8e17-fdbfab77341b`) — the app `Connect-ExchangeOnline` uses; exists in
  every tenant, supports device code. Override with `EXO_CLIENT_ID` if a tenant blocks it.
- Scopes: `https://outlook.office365.com/.default offline_access openid profile`
  (override: `EXO_SCOPES`). The signing user must be an **Exchange admin** in that client tenant.
- Storage is identical to Graph's (D-37): secrets in the client's CredVault entry **`exo_oauth`**,
  non-secret health sidecar `vault/clients/<tenant>/exo.json`. Same auto-renew keep-alive, same
  locked-vault semantics, same Memory-tab visibility.
- The M365 card lists BOTH connections per client: "Graph" and "Exchange", each with its own
  Sign in / Disconnect / health.

## The client (`clients/exo.py`) — InvokeCommand with a hard cmdlet allowlist

`POST https://outlook.office365.com/adminapi/beta/<tenant-guid>/InvokeCommand` with
`{"CmdletInput": {"CmdletName": "...", "Parameters": {...}}}` (+ `X-ResponseFormat: json`,
`X-AnchorMailbox: UPN:<signing admin>` from the token claims). Security model:

- **CmdletName must EXACTLY match the allowlist** — there is no path for the model (or a tool)
  to invoke an arbitrary cmdlet. Parameters are JSON data, never PowerShell text, so there is no
  script-injection surface.
- Allowlist (extend deliberately, never AI-improvised — same rule as READ_SCOPES):
  - reads: `Get-Mailbox`, `Get-MailboxPermission`, `Get-RecipientPermission`, `Get-AcceptedDomain`
  - writes: `New-Mailbox` (the client FORCES `Shared: true` — this connector can only ever
    create *shared* mailboxes, not licensed users), `Add-MailboxPermission`,
    `Add-RecipientPermission`
  - destructive: none. `Remove-*` / `Set-*` are not in the list and cannot be called.
- Fail-closed: not signed in → no client (Rule #8); unknown cmdlet → refused before any HTTP.

## Capabilities

- `exo_list_mailboxes` (read): `Get-Mailbox` slim listing (name, type, primary SMTP) — lets the
  agent verify its own writes. Enabled by default.
- `exo_create_shared_mailbox` (**write**, REQUIRES_APPROVAL=True, disabled until the owner
  enables it + opens allow_write): creates the shared mailbox, then optionally grants
  Full Access (`Add-MailboxPermission`, with AutoMapping) and Send As
  (`Add-RecipientPermission`) to a named user. Partial failures are reported per step so the
  owner can see exactly what happened.

Every dispatch guardrail applies unchanged: capability toggle (I-4), allow_write + per-run
approval (Rule #1), tenant binding, audit, cloud gating.

## Honest caveats

- The EXO admin REST endpoint is `beta`-versioned and Microsoft has changed it before; if
  InvokeCommand answers 4xx with a schema complaint, check the EXO PowerShell module's current
  wire format.
- Tenants with Conditional Access blocking device code, or app-governance blocking the EXO
  PowerShell app, need their own app registration (`EXO_CLIENT_ID`) with EXO delegated access.
- The "Exchange administrator" role (not just any admin) is required by the API server-side;
  a Global Admin works too. Errors surface as 401/403 with the role hint.

## Amendment (2026-06-11) — grouping + unlock sweep
- The EXO skills carry `SOURCE = "m365"` so they appear in the **Microsoft 365 group** in the
  Capabilities tab (and cite as `@m365`) — they're Office-365 tools to the owner. The vendor
  CLIENT is still selected by `ctx.client("exo")`; SOURCE is only the grouping/citation label.
- Tokens connected while the credential vault is LOCKED fall back inline to the sidecar (D-37).
  `m365_auth.migrate_inline_secrets(cfg)` now runs on every vault **unlock** (the `_cv_unlock`
  route), sweeping any inline M365/EXO secrets into `credentials.enc` immediately — so a token
  connected during a locked window doesn't sit in plaintext until its next use. (Turning on agent
  auto-unlock avoids the locked window entirely.)

## Amendment (2026-06-11, D-55) — mailbox administration suite

Owner requested the day-to-day MSP mailbox operations. Design rules that keep the surface bounded:

- **Allowlist grows deliberately** (hand-edited, never AI-improvised). New reads:
  `Get-MailboxStatistics`, `Get-DistributionGroup`, `Get-DistributionGroupMember`,
  `Get-UnifiedGroup`, `Get-UnifiedGroupLinks`, `Get-RetentionPolicy`. New writes:
  `Set-Mailbox`, `Add-DistributionGroupMember`, `Add-UnifiedGroupLinks`,
  `Enable-Mailbox`, `Disable-Mailbox`.
- **Per-cmdlet PARAMETER allowlist** (`PARAM_ALLOWLIST`): `Set-Mailbox` is broad, so the
  connector also bounds WHICH parameters may travel — only the ones our skills need
  (GAL visibility, primary SMTP, aliases, type, quotas, forwarding, retention). An
  unknown parameter is refused before any HTTP, so even a promoted AI draft can't use
  `Set-Mailbox` to reach, say, litigation hold or audit bypass.
- **Forced parameters** (`FORCED_PARAMS`, generalizes the old New-Mailbox Shared:true rule):
  `Disable-Mailbox` is FORCED to `Archive: true`; `Enable-Mailbox` must be an ARCHIVE
  operation — when neither `Archive` nor `AutoExpandingArchive` is true the connector forces
  `Archive: true` (the two are different Exchange parameter sets, so they can't both be
  forced). Through this connector they can only ever touch the ONLINE ARCHIVE, never
  mailbox-disable/-enable an account (that would be destructive; deletion stays on D-54).
  Both cmdlets are also parameter-allowlisted to exactly {Identity, Confirm, archive switches}.
- `exo_enable_autoexpanding_archive` (write, D-55 follow-up): `Enable-Mailbox
  -AutoExpandingArchive` per mailbox. ENABLE-ONLY by Microsoft's design — auto-expanding
  archiving CANNOT be turned off once on, so the tool says so up front and the result repeats
  it. Preflights: the regular archive must already be enabled (point the owner at
  exo_set_archive first); already-on → clean no-op. Verifies `AutoExpandingArchiveEnabled`
  after. Honest caveats: needs EXO Plan 2 / E3+, extra space provisions gradually (not
  instant), grows to ~1.5 TB max.
- **Hashtable parameters** (e.g. `EmailAddresses @{Add=...}`) serialize as
  `{"@odata.type": "#Exchange.GenericHashTable", ...}` — `exo.hashtable()` builds them.
- **Every write skill verifies its own write**: preflight `Get-Mailbox` (target must exist,
  resolve to exactly one), apply, then re-read and compare — a write that didn't stick is
  reported as a failure, never as success (D-43 lesson). Shared helper:
  `skills/_exo_common.py` (no NAME attr → invisible to the registry, I-1).

Skills added (all SOURCE=m365; writes are REQUIRES_APPROVAL=True + ENABLED_BY_DEFAULT=False):
- `exo_mailbox_details` (read): one mailbox's full admin view — type, aliases, GAL visibility,
  forwarding, quotas, retention policy, archive status + primary/archive mailbox SIZES
  (`Get-MailboxStatistics`, `-Archive`).
- `exo_set_gal_visibility` (write): hide/unhide from the Global Address List.
- `exo_set_primary_smtp` (write): change the primary SMTP address (+ sign-in UPN via
  `MicrosoftOnlineServicesID` so User ID and email stay in lockstep); old address is kept
  as an alias by Exchange.
- `exo_add_alias` (write): add a proxy address (`EmailAddresses @{Add="smtp:..."}`).
- `exo_convert_mailbox` (write): shared ⇄ regular (`Set-Mailbox -Type`); honest caveat:
  converting TO regular requires assigning a license afterwards.
- `exo_list_groups` (read): distribution groups + mail-enabled security + Microsoft 365
  groups (`Get-DistributionGroup` ∪ `Get-UnifiedGroup`), each tagged with its kind.
- `exo_add_group_member` (write): resolves the group's kind first, then
  `Add-DistributionGroupMember` or `Add-UnifiedGroupLinks -LinkType Members`; verifies
  membership after.
- `exo_set_mailbox_quota` (write): `MaxSendSize`/`MaxReceiveSize` ("35MB" style, 1–150 MB —
  the EXO hard ceiling).
- `exo_set_forwarding` (write): SMTP forwarding on/off + `DeliverToMailboxAndForward`
  (keep a copy or not); empty address clears forwarding.
- `exo_list_retention_policies` (read) + `exo_set_retention_policy` (write): policy must be
  one of the tenant's existing policies (preflight `Get-RetentionPolicy`).
- `exo_set_archive` (write): enable/disable the online archive (forced `-Archive`, above).

- **Retention management** (D-55 follow-up): the MRM building blocks — a retention TAG is one
  rule ("delete after 90 days", "archive after 2 years"); a retention POLICY is a named bundle
  of tags; a mailbox gets exactly one policy (`exo_set_retention_policy`, above).
  - `exo_list_retention_tags` (read): `Get-RetentionPolicyTag` — name, scope, action, age.
  - `exo_create_retention_tag` (write): `New-RetentionPolicyTag` — name + applies_to (folder
    scope: All/Personal/Inbox/…) + action (`delete_allow_recovery` | `permanent_delete` |
    `move_to_archive`) + age_days. Friendly action names map to Exchange's RetentionAction;
    `permanent_delete` carries an explicit warning in the result; `move_to_archive` is valid
    only for All/Personal scopes (refused otherwise — Exchange's own rule, surfaced early).
    Duplicate name → refused.
  - `exo_create_retention_policy` (write): `New-RetentionPolicy` with `RetentionPolicyTagLinks`;
    every named tag must already exist (preflight `Get-RetentionPolicyTag`), duplicate policy
    name refused; verified by re-reading the policy.
  - `exo_update_retention_policy_tags` (write): `Set-RetentionPolicy` with the
    `RetentionPolicyTagLinks` hashtable `@{Add=…/Remove=…}` — add/remove tags on an existing
    policy; tags-to-add must exist; verified against the re-read link list.
  - Allowlist additions: reads `Get-RetentionPolicyTag`; writes `New-RetentionPolicyTag`,
    `New-RetentionPolicy`, `Set-RetentionPolicy` — each parameter-allowlisted (PARAM_ALLOWLIST)
    to exactly the fields above, so nothing AI-drafted can reach other MRM knobs.
- `exo_grant_mailbox_access` (write, D-55 follow-up): grant ONE user ONE access type on any
  mailbox (shared or regular) — `full_access` (`Add-MailboxPermission`, with Outlook automap
  switch), `send_as` (`Add-RecipientPermission`), or `send_on_behalf` (`Set-Mailbox
  -GrantSendOnBehalfTo @{Add=...}` — the param is added to PARAM_ALLOWLIST). Each grant is
  verified by re-reading the matching permission list (`Get-MailboxPermission` /
  `Get-RecipientPermission` / the mailbox's `GrantSendOnBehalfTo`); already-granted is a clean
  no-op. One access type per call keeps each approval card unambiguous. Caveat surfaced in-tool:
  Exchange stores send-on-behalf entries as display names, and Full Access automapping can take
  ~an hour to appear in Outlook.

Also fixed here: `exo_create_shared_mailbox` now pins `MicrosoftOnlineServicesID` + `Alias`
to the requested primary address (Exchange was deriving the User ID from the display name —
"AI Test" → `AITest@…` while the email was `ai-test@…`), and takes `first_name`/`last_name`
(the agent is instructed to ASK for them rather than invent or omit).

## Amendment (2026-06-11, D-54) — a DESTRUCTIVE tier: mailbox deletion (owner-approved)
The owner explicitly requested delete capability. Design:
- `EXOClient` gains a SEPARATE destructive path: `DESTRUCTIVE_CMDLETS = {Remove-Mailbox}` reachable
  ONLY via `invoke_destructive()`. The normal `invoke()` still refuses every `Remove-*` — so no
  read/write tool (including anything AI-drafted) can reach deletion by accident.
- The candidate validator rejects any generated tool that touches `invoke_destructive` — AI drafts
  can never be destructive (I-5 floor unchanged); destructive tools are hand-written only.
- New hand-written skill `exo_delete_mailbox` (CATEGORY=destructive, disabled by default):
  pre-flight `Get-Mailbox` (reports the mailbox type and refuses ambiguity), `Remove-Mailbox`
  (soft delete — recoverable in Exchange ~30 days), then a verify pass that the mailbox is gone.
  Honest caveats in-tool: deleting a USER mailbox deletes that user account (Exchange behavior);
  a shared mailbox is just removed. Destructive floor (Rule #1): per-run approval can NEVER be
  toggled off; allow_write must also be opened; every run audited.

## Amendment (2026-06-11, D-56) — distribution groups + mail contacts (create)
- `exo_create_distribution_group` (write): `New-DistributionGroup` (param-allowlisted: Name/
  DisplayName/Alias/PrimarySmtpAddress/Type/Members/Confirm). Type pinned to "Distribution";
  members can be seeded at create or added later with `exo_add_group_member`. Duplicate name
  refused; verified via `Get-DistributionGroup`.
- `exo_create_contact` (write): `New-MailContact` — an external person in the client's address
  book (Name + ExternalEmailAddress + optional First/Last). Duplicate external address refused;
  verified via `Get-MailContact`. Reads: `Get-MailContact` added to the allowlist.

## Amendment (2026-06-11, D-58) — permissions reporting, folder access, mail hygiene, compliance alerts

**Security & Compliance endpoint (no third sign-in).** `New-ProtectionAlert` lives in the S&C
PowerShell (`Connect-IPPSSession`), a DIFFERENT REST host + token audience
(`ps.compliance.protection.outlook.com`). Same first-party app as EXO though — so
`m365_auth.compliance_token(cfg, tenant)` redeems the client's EXISTING EXO refresh token for the
compliance scope (exactly what Connect-IPPSSession does after one sign-in). Cached in-process;
a rotated refresh token is persisted back to the exo store. `EXOClient.invoke_compliance()` is a
THIRD path next to invoke/invoke_destructive with its own tiny allowlist
(`COMPLIANCE_CMDLETS = Get-/New-ProtectionAlert`) and param allowlist; the signing admin needs a
compliance role (errors surface with that hint).

**Allowlist additions** — reads: `Get-MailboxFolderPermission`, `Get-MailboxJunkEmailConfiguration`,
`Get-TransportRule`, `Get-InboundConnector`. Writes (all param-allowlisted):
`Add-/Set-MailboxFolderPermission` (Identity/User/AccessRights only),
`Set-MailboxJunkEmailConfiguration` (Identity/Enabled), `New-TransportRule` (the
auto-forward-block + spam-bypass shapes only), `New-InboundConnector` (the Proofpoint shape).

Skills:
- `exo_list_mailboxes` gains `type` (all|shared|user|room) → `RecipientTypeDetails` filter ("list
  all shared mailboxes").
- `exo_mailbox_permissions` (read): WHO can access a mailbox — Full Access
  (`Get-MailboxPermission`), Send As (`Get-RecipientPermission`), Send on Behalf; system rows
  (NT AUTHORITY\SELF, S-1-5-*, Default) filtered out. No identity → sweeps ALL SHARED mailboxes
  (capped, default 50).
- `exo_user_mailbox_access` (read): the REVERSE — every mailbox a user can access. N+1 sweep over
  mailboxes (capped, default 100, noted), checking the three permission kinds per mailbox.
- `exo_grant_folder_access` (write): calendar/contacts delegation —
  `Add-MailboxFolderPermission "<mb>:\Calendar"` (or `:\Contacts`); friendly rights map
  (owner/editor/author/reviewer/contributor/availability_only/limited_details). If the user
  already has rights on the folder it switches to `Set-` (Add- errors on existing). Verified by
  re-reading the folder permission. Caveat: folder paths assume English folder names ("Calendar"/
  "Contacts") — localized tenants may need the localized name.
- `exo_user_folder_access` (read): which calendars/contacts a user was granted (N+1 sweep,
  capped; only EXPLICIT grants — the tenant-wide Default:AvailabilityOnly isn't a grant).
- `exo_block_auto_forwarding` (write): the owner's standard transport rule (InOrganization →
  NotInOrganization, MessageTypeMatches=AutoForward, reject text) — exists → clean no-op.
- `exo_junk_filter_status` (read) + `exo_set_junk_filter` (write): per-mailbox or ALL user
  mailboxes (`Set-MailboxJunkEmailConfiguration`); bulk mode reports applied/failed counts and
  verifies a sample of 5 (per-mailbox verify on single mode). Known Exchange quirk surfaced:
  never-logged-on mailboxes can refuse junk configuration.
- `exo_setup_proofpoint_bypass` (write): the owner's two-step Proofpoint Essentials setup —
  transport rule (SenderIPRanges → SetSCL -1) + inbound connector (RestrictDomainsToIPAddresses,
  RequireTls, the PP US ranges). IP ranges are module constants (the owner's exact lists);
  idempotent per step; each verified.
- `exo_add_forwarding_alert` (write, via invoke_compliance): `New-ProtectionAlert` on
  Operation=MailRedirect notifying a REQUIRED `notify_email` (the tool's schema forces the agent
  to ask which address). Exists-by-name → no-op; verified by `Get-ProtectionAlert`.

## Amendment (2026-06-12, D-63) — REVOKE capabilities (the missing half of access management)

Owner hit it live: two AI-drafted tools (exo_remove_mailbox_permission, exo_remove_folder_access)
were promoted but could only REFUSE — the draft validator rightly stops generated code from
improvising around the connector allowlist, and the allowlist had no removal cmdlets. Replaced
both drafts with hand-written tools + deliberate allowlist additions.

**The destructive line, restated:** `destructive` = irreversible DATA loss (Remove-Mailbox, D-54
path only). Revoking a PERMISSION destroys no data and is undone by re-granting — so
`Remove-MailboxPermission`, `Remove-RecipientPermission`, `Remove-MailboxFolderPermission` join
ALLOWED_CMDLETS as param-allowlisted WRITES (normal approval gate), and the old "no Remove-* in
the write allowlist" phrasing above is superseded by this rule.

- `exo_revoke_mailbox_access` (write): mirror of exo_grant_mailbox_access — remove ONE user's
  full_access (Remove-MailboxPermission) / send_as (Remove-RecipientPermission) /
  send_on_behalf (Set-Mailbox GrantSendOnBehalfTo @{Remove=...}). Not-held → clean no-op;
  every revoke verified by re-reading the permission list (gone = success, still there = loud
  failure).
- `exo_revoke_folder_access` (write): mirror of exo_grant_folder_access —
  Remove-MailboxFolderPermission on "<mb>:\Calendar" / ":\Contacts"; absent → no-op; verified.

## Amendment (2026-06-12, D-65) — REMOVE counterparts (add/remove symmetry audit)

Owner audit: every "add/create" should have its "remove/delete". Built the missing counterparts.
Classification stays the D-63 line: destructive = mailbox-CONTENT data loss (exo_delete_mailbox
only). Deleting a DL / contact / retention object / transport rule / connector destroys no mailbox
content and is re-creatable, so these are WRITES (high risk, normal approval gate), each verified
gone after.

Allowlist additions (writes, param-allowlisted): Remove-DistributionGroupMember,
Remove-UnifiedGroupLinks, Remove-DistributionGroup, Remove-MailContact, Remove-RetentionPolicyTag,
Remove-RetentionPolicy, Remove-TransportRule, Remove-InboundConnector; compliance:
Remove-ProtectionAlert.

New EXO skills (each: not-present → clean no-op; verify-gone after): exo_remove_alias (Set-Mailbox
EmailAddresses @{Remove}), exo_remove_group_member (DL + M365 group), exo_delete_distribution_group,
exo_delete_contact, exo_delete_retention_tag, exo_delete_retention_policy,
exo_unblock_auto_forwarding (removes the block rule), exo_remove_proofpoint_bypass (rule +
connector), exo_remove_forwarding_alert (compliance).

## Amendment (2026-06-12, D-66) — docs-audit fixes (4-agent sweep vs KB + Microsoft Learn)
- `exo_create_shared_mailbox`: New-Mailbox sign-in param is **UserPrincipalName**, not
  MicrosoftOnlineServicesID — the latter is a different parameter set and conflicts with -Shared
  (would fail to resolve). Fixed.
- `exo_add_forwarding_alert`: New-ProtectionAlert -Severity enum is Low|Medium|High;
  "Informational" is invalid → now "Low".
- `exo_setup_proofpoint_bypass` + exo.py allowlist: New-InboundConnector now passes
  **ConnectorType="Partner"** (added to PARAM_ALLOWLIST). RestrictDomainsToIPAddresses / RequireTls
  / SenderIPAddresses apply ONLY to Partner connectors — without it the IP lock-down + TLS
  requirement silently didn't take effect (security loosening).
- `exo_create_retention_tag`: move_to_archive is valid for Type All/Personal AND **RecoverableItems**
  (was wrongly rejecting RecoverableItems).

## Amendment (2026-06-12, D-67) — quota accepts GB
`exo_set_mailbox_quota` now parses KB/MB/**GB** (e.g. "0.1GB" ≈ 102MB) and sends Exchange a
canonical "<int>MB" so the verify echo-match stays exact. The 1MB–150MB EXO ceiling is unchanged
(a too-large value reports the computed MB).

## Amendment (2026-06-22, D-91) — enable Exchange cloud management for AD-synced users

`exo_enable_cloud_management` (CATEGORY=write, RISK=medium, approval-required, default-off). For a
user synced from on-prem AD (`IsDirSynced=True`), Exchange mailbox settings are mastered on-prem and
can't be edited in EXO until the mailbox is flagged cloud-managed; this tool runs
`Set-Mailbox -IsExchangeCloudManaged $true` so the existing cloud mailbox tools
(`exo_set_gal_visibility`, `exo_add_alias`, `exo_set_primary_smtp`) take effect. AD still owns
identity, password, enabled/disabled status, and group membership — only mailbox settings move to
the cloud.

- `IsExchangeCloudManaged` added to `Set-Mailbox`'s PARAM_ALLOWLIST in `exo.py` (the one new
  parameter this needs; nothing else widened).
- Same D-43 verify discipline as the rest of the suite: preflight Get-Mailbox (exists + reads
  `IsDirSynced` / `IsExchangeCloudManaged`) → Set-Mailbox → re-read → confirm the flag flipped.
  Already-managed mailboxes short-circuit to ok ("nothing to do"); a non-AD-synced mailbox is noted
  (it's typically already cloud-managed) but not refused.
- Carries a `describe_approval` (D-90) so the approval card reads "Enable cloud management for:
  <user>" with the plain-English effect, not a bare cmdlet.
- The downstream asks (update GAL visibility + confirm; set primary SMTP) are already covered by the
  existing `exo_set_gal_visibility` and `exo_set_primary_smtp` tools — both self-verify — so this
  enable step is the only missing piece in the GAL/alias/SMTP workflow for synced users.
- **Read-side:** `exo_mailbox_details` now surfaces `dir_synced` (IsDirSynced) and `cloud_managed`
  (IsExchangeCloudManaged) — both already in the Get-Mailbox response, just not exposed — so "is
  cloud management already set?" is answerable with a READ, no write/approval needed.
- **Preflight guard:** changing an on-prem-mastered attribute (GAL visibility, aliases, primary
  SMTP / sign-in UPN) on a directory-synced, NOT-yet-cloud-managed mailbox returns Exchange's
  cryptic `400 "out of the current user's write scope"`. `_exo_common.needs_cloud_management(mb,
  params, label)` (keyed on `_DIRECTORY_MASTERED` = {HiddenFromAddressListsEnabled, EmailAddresses,
  WindowsEmailAddress, MicrosoftOnlineServicesID}) now pre-empts this in preflight — BEFORE any
  Set-Mailbox — with `ok=false, step=preflight, needs_cloud_management=true` and a message that
  names `exo_enable_cloud_management` as the fix. Wired into `set_and_verify` (covers
  `exo_set_gal_visibility`, `exo_set_primary_smtp`) and called explicitly by `exo_add_alias` /
  `exo_remove_alias` (they invoke Set-Mailbox directly). The agent now explains the cause and offers
  to enable cloud management up front, instead of attempting the write and reverse-engineering the
  400 afterward.

## Amendment (2026-06-22, D-96) — bulk GAL visibility + higher tool-call round cap

"Check these 20 users' GAL and hide the ones that aren't" repeatedly hit the agent's tool-call
round cap: one `exo_mailbox_details`/`exo_set_gal_visibility` per user burns a ROUND per mailbox, so
the loop died (no answer) before finishing even the check phase. Two fixes:
- **`exo_bulk_set_gal_visibility`** (CATEGORY=write, RISK=medium, approval-required, default-off):
  takes a LIST of mailbox addresses + `hidden`, and in ONE call (one round, ONE approval) per
  mailbox skips those already in the desired state, sets + re-reads to verify the rest (D-43), and
  flags any blocked by the cloud-management guard (D-91) — never failing the whole batch for one
  bad mailbox. Returns a per-mailbox result table + summary counts. Carries `describe_approval`
  (D-90) so the owner approves the whole batch once. The agent should prefer it over calling
  `exo_set_gal_visibility` one mailbox at a time.
- **Round cap raised 8 → 20** (`runtime.build_agent`, override `MSPAI_MAX_ROUNDS`): 8 was too low
  for any multi-step/per-item task; 20 gives headroom while still bounding runaway loops.
Test: `test_bulk_gal_handles_mixed_states_in_one_call` (hidden / unchanged / needs_cloud_management
/ error in a single call; only the one real change writes).

## Amendment (2026-06-23, D-97) — list a mailbox FOLDER's permissions (the missing read)

Owner hit it live ("calendar permissions for corp-pto"): we had the grant/revoke writes
(`exo_grant_folder_access` / `exo_revoke_folder_access`) and the per-USER reverse sweep
(`exo_user_folder_access`), but no direct "who is on THIS mailbox's calendar?" read — so the agent
fell back to `exo_mailbox_permissions` (mailbox-level Full Access / Send As / Send on Behalf), which
answers a different question. `Get-MailboxFolderPermission` was already in the read allowlist (used
by the grant/revoke verify steps), so no connector change — just the missing skill.

- `exo_folder_permissions` (read, default-on): one mailbox + folder (calendar|contacts, default
  calendar) → `Get-MailboxFolderPermission "<mb>:\Calendar"` with NO User filter, returning every
  entry (user + AccessRights). Preflight `get_one_mailbox` resolves the primary SMTP; localized
  folder-name caveat surfaced as a clear error (same as grant). Unlike `exo_mailbox_permissions`,
  the `Default`/`Anonymous` rows are KEPT (flagged `well_known`) — on a calendar they are the
  tenant-wide / external free-busy baseline, not noise — and sorted after the individual grants.
  Complements `exo_user_folder_access` (per-user across mailboxes) as the per-folder view.

## Amendment (2026-06-23, D-98) — folder-permission match is by DISPLAY NAME, not address

Live failure (RHO_Residential, mutual calendar shares): `exo_grant_folder_access` reported
"Add returned no error but the Calendar permission doesn't show 'Reviewer' for SGrosso@…" and then,
on retry, a hard `EXO HTTP 400 UserAlreadyExistsInPermissionEntryException — An existing permission
entry was found for user: Susan Grosso.` Both are ONE bug.

**Root cause:** `Get-MailboxFolderPermission` echoes each entry's `User` as the resolved DISPLAY
NAME ("Susan Grosso"), NOT the address passed to `Add-MailboxFolderPermission`. The old
`_user_entry` matched `User` only against the email and its local-part (`sgrosso@…` / `sgrosso`), so
for anyone whose display name ≠ email prefix it ALWAYS returned "not found." Consequences chained:
(1) the post-write verify re-read couldn't find the row it had just created → **false-negative
failure**; (2) the next preflight still couldn't see the entry → chose `Add-` not `Set-` → Exchange
rejected the duplicate (the 400). The grant had in fact SUCCEEDED on the first attempt.

**Fix (D-98):**
- New `exo_grant_folder_access.identifiers(exo, user)` resolves the target via one `Get-Mailbox` and
  returns the set {address, local-part, DisplayName, Alias, Name, UPN} (all lowercased).
  `_user_entry(rows, user, idents)` now matches the row's `User` against that set — so the
  display-name row is found on both preflight and verify.
- Self-heal: if we chose `Add-` but Exchange returns `…AlreadyExists…`, switch to
  `Set-MailboxFolderPermission` and proceed (covers any residual name-resolution gap, e.g. localized
  or unusual display strings).
- `exo_revoke_folder_access` (shares `_user_entry`) updated identically — it was silently reporting
  "nothing to remove" for grants it couldn't see.
- The new read `exo_folder_permissions` (D-97) intentionally returns the raw `User` (display name) —
  which is what surfaced this. No change there.

Tests (fake EXO): fresh grant verifies via display name; already-held → idempotent no-op;
Add-→Set- self-heal on the 400; revoke removes a display-name entry.

**Lesson:** any client-side match on a `Get-MailboxFolderPermission` / `Get-MailboxPermission` `User`
field must resolve the recipient's display name + alias first — never compare the raw address alone.

## Amendment (2026-06-23, D-99) — mailbox-type conversion is eventually-consistent (poll, don't verify once)

Live failure (offboarding mpascal): `exo_convert_mailbox` reported "convert mailbox: the change did
not stick — check Exchange directly" even though Set-Mailbox -Type returned no error. Root cause:
`set_and_verify` does ONE immediate Get-Mailbox re-read, but a Type conversion is
eventually-consistent — Exchange accepts it and keeps reporting the OLD `RecipientTypeDetails`
(UserMailbox/SharedMailbox) for several seconds (sometimes a minute+) before it flips. The single
read saw the stale value → false "did not stick."

Fix: `exo_convert_mailbox` no longer uses `set_and_verify`. It runs Set-Mailbox directly, then POLLS
Get-Mailbox (6 × 2s) until `RecipientTypeDetails` matches the target. If it flips → success. If the
window elapses → `ok=false, pending=true` with a message that says Exchange ACCEPTED the change and
it's almost certainly propagation lag — re-check with `exo_mailbox_details` shortly and do NOT re-run
the convert (it likely already took). This stops the scary false-failure and the pointless retry.
Tests: flips-after-N-polls → success; never-flips → pending (not "did not stick"); already-target →
no-op without calling Set-Mailbox.

## Amendment (2026-06-23, D-105) — authoritative distribution-group membership (Get-Recipient)

Graph `memberOf` (m365_user_groups) reliably lists security/M365 groups but can MISS classic
distribution lists — Exchange is the authoritative source. Added read-only `Get-Recipient` to the EXO
connector allowlist (deliberate addition) and a new `exo_user_distribution_groups` (read, default-on):
resolves the user's `DistinguishedName` via Get-Mailbox, then `Get-Recipient -Filter "Members -eq
'<DN>'"` (apostrophes doubled) → every distribution / mail-enabled security / M365 / dynamic group the
user is a DIRECT member of, classified with `removable` + `remove_with` (exo_remove_group_member;
dynamic = not manually removable). `memberships(ctx, user)` is the reusable core m365_offboard_user
calls for its group-cleanup listing (D-105). Used together: EXO covers all MAIL-enabled groups
(authoritative), Graph covers non-mail security groups — a clean, non-overlapping split.

## Amendment (2026-06-24, D-109) — user-mailbox-access sweep was 300-capped (missed mailboxes)

Live offboard of dtmtester@RHO reported 0 mailbox grants but the user DID have Full Access + Send-As
on the shared mailbox `thealtiers@`. Root cause: `exo_user_mailbox_access` swept `Get-Mailbox`
hard-capped at `ResultSize: 300`, in identity order — a 't' mailbox past the first 300 was never
checked, so BOTH its Full Access and Send-As were silently skipped. Rewrote the tool (D-109):
- **No cap by default** — `ResultSize: Unlimited` (a `limit` param can still bound a huge tenant). The
  sweep walks every mailbox for Full Access (no reverse query exists) + per-box Send-As + Send-on-Behalf.
- **Direct Send-As reverse lookup** — `Get-RecipientPermission -Trustee <user>` returns every recipient
  the user can send as in ONE call, independent of the sweep. For each result not already swept, resolve
  it (Get-Mailbox) and ALSO check its Full Access — so a mailbox the sweep missed still reports COMPLETE
  access. This guarantees a both-perms mailbox like thealtiers@ is caught even if the sweep doesn't list it.
- Results are deduped by primary SMTP; `mailboxes_checked` reports sweep coverage.
m365_offboard_user now calls it with no limit (full sweep). Tests: reverse lookup (with the new direct
call), and a direct-Send-As-catches-a-swept-miss case. Trade-off: the unbounded sweep is slower on big
tenants, but the D-101 heartbeat shows progress and offboarding favors completeness over speed.

## Amendment (2026-06-24, D-110) — exo_mailbox_details takes a batch list

Same one-call-per-item fix as the M365 Graph reads: `exo_mailbox_details` now accepts `identities[]`
alongside `identity`. The batch path returns `{ok, mailboxes_checked, results:[ <per-mailbox dict> ]}`,
each row carrying its own `mailbox` so a miss/error is attributable. Body refactored into `_one(exo, ...)`
sharing the single EXO client; `identity` dropped from `required` so a list-only call validates.
DESCRIPTION leads with "do NOT call this tool once per mailbox." Test: details-batches-in-one-call.

### Follow-up (D-110) — exo_grant_folder_access batches recipients

Owner saw the agent call `exo_grant_folder_access` ~52× to share one mailbox's calendar with a whole
team. It now accepts `users[]` (recipients) alongside `user`: ONE call grants the same folder/level to
the whole list under ONE approval (the write-batch precedent is D-96 `exo_bulk_set_gal_visibility`). The
mailbox preflight (`get_one_mailbox`) runs once; the per-recipient grant logic is factored into
`_grant(exo, mailbox, fid, fname, right, access, user)` and still applies + VERIFIES each grant (the
single-user call sequence is unchanged — Get-Mailbox · Get-Mailbox · Get-MailboxFolderPermission ·
Add/Set · Get-MailboxFolderPermission). Returns `{ok, mailbox, folder, access, users_granted, summary:
{granted,unchanged,error}, results:[ per-user ]}`; each per-user row (incl. failures) carries its `user`.
`user` dropped from `required` (mailbox/folder/access still required). The same shape fits the siblings
`exo_revoke_folder_access`, `exo_grant_mailbox_access`, `exo_revoke_mailbox_access` if they start looping.
Test: grant-folder-access-batches-recipients-in-one-call.

## Amendment (2026-06-26, D-113) — force-start the Managed Folder Assistant

After applying a retention policy (`exo_set_retention_policy`) or enabling the online archive
(`exo_set_archive`), the rules only take effect when the **Managed Folder Assistant (MFA)** next
processes the mailbox — on its own ~7-day cycle. Owners (and the AdminToolKit) routinely kick it off
immediately. The agent had no enabled tool for this, so it correctly declined rather than improvise.

New tool **`exo_start_archive`** (write, RISK medium, approval-gated, default-off). It mirrors the
proven AdminToolKit flow: resolve the mailbox's **Primary GUID**, then
`Start-ManagedFolderAssistant <guid>`. The Primary GUID is `Get-Mailbox`'s **`ExchangeGuid`** — exactly
what `Get-MailboxLocation` returns for the Primary location — so it comes free from the standard
preflight read; we do NOT need to add `Get-MailboxLocation` to the allowlist, and we target the GUID
(not the email) so the right mailbox is processed once an archive mailbox also exists. THIS call creates
and deletes nothing — it only triggers the already-scheduled assistant to run sooner; the actual
retention/archive tagging still happens asynchronously over minutes-to-hours, so there is no synchronous
state to verify (the tool reports that the assistant was started, with the targeted `primary_guid`).

Allowlist addition (deliberate, minimal): `Start-ManagedFolderAssistant` → write, param-allowlisted to
`{Identity}` only. Batches via `identities[]` (D-110 pattern): one call → many mailboxes →
`{ok, started, ok_count, results:[ per-mailbox ]}`, each row carrying its `identity`/`primary_guid`.
Test: tests/test_exo_start_archive.py.

## Amendment (2026-06-26, D-114) — tenant-wide mailbox usage / storage-triage report

New read tool **`exo_mailbox_usage_report`** (read, default-on) answers "which mailboxes are near full,
and are they archiving / on what retention policy?" in ONE call so the owner can decide a procedure
(enable archive, set retention, etc.). It lists every mailbox (`Get-Mailbox`, type/limit filterable) and
per mailbox reports size, quota, **percent full**, archive state (+ archive size if on), and retention
policy. `min_percent` (e.g. 90) filters to the near-full ones; results sort fullest-first.

Efficiency note: the QUOTA comes free from the listing's `ProhibitSendQuota`, so only the SIZE needs a
per-mailbox `Get-MailboxStatistics` — one stat call per mailbox (two if it has an archive), streamed via
`ctx.map_progress` so the scan shows live "n/total" progress. Percent is computed from the parsed
`(… bytes)` tail; when `ProhibitSendQuota` is "Unlimited"/DB-default with no explicit value the tool
assumes the EXO 100 GB default and flags the row `quota_assumed:true` (honest, not silent). No new
cmdlets — uses the already-allowlisted `Get-Mailbox` + `Get-MailboxStatistics`. Generic across all
clients; client-specific knowledge (e.g. RHO's Policy 1/2/3 → policy-name + title→policy mapping) lives
in that client's vault `memory.md`, NOT in the tool. Test: tests/test_exo_mailbox_usage_report.py.

### Follow-up (D-114) — exo_describe_retention_policies (policies with tags expanded)

`exo_list_retention_policies` shows a policy's tag NAMES; `exo_list_retention_tags` shows what each tag
does — but deciding "which policy do I apply?" on a client without a standard set meant cross-referencing
the two by hand. New read tool **`exo_describe_retention_policies`** (default-on) joins them: for each
policy it expands every linked tag to `{action, age_days, applies_to(scope), enabled}` plus a
plain-English per-tag summary (`move to archive @ 730d [All]`, `delete (PERMANENT) @ 2555d [All]`) and a
one-line policy summary. `name` filters to one policy. A linked tag that no longer exists in the tag list
is surfaced under `unresolved_tags` (not dropped silently). RetentionAction→label map mirrors
exo_create_retention_tag's `_ACTIONS` reversed. No new cmdlets (Get-RetentionPolicy + Get-RetentionPolicyTag,
both already allowlisted). Test: tests/test_exo_describe_retention_policies.py.

## Amendment (2026-06-30, D-115) — Content Search (Purview eDiscovery), phase 1: create → start → status → preview

**Same compliance endpoint, no new sign-in.** Content Search is the `*-ComplianceSearch` cmdlet family on
the SAME Security & Compliance PowerShell endpoint (`ps.compliance.protection.outlook.com`) the D-58
ProtectionAlert tools already reach via `EXOClient.invoke_compliance()` — so no new client, audience, or
device-code flow. We extend the tiny compliance allowlist; everything else (token redemption, param
allowlist enforcement, audit) is reused unchanged.

**Allowlist additions** (`COMPLIANCE_CMDLETS` + `PARAM_ALLOWLIST`, both in `clients/exo.py`):
- reads: `Get-ComplianceSearch` (`Identity`, `ResultSize`), `Get-ComplianceSearchAction`
  (`Identity`, `Details`, `ResultSize`).
- writes: `New-ComplianceSearch` (`Name`, `ExchangeLocation`, `ExchangeLocationExclusion`,
  `ContentMatchQuery`, `Description`, `AllowNotFoundExchangeLocationsEnabled`),
  `Start-ComplianceSearch` (`Identity`), `New-ComplianceSearchAction` (`SearchName`, `Preview`).

**Safety property — Export/Purge are structurally unreachable.** `New-ComplianceSearchAction` is the cmdlet
that would also do `-Export` (download) and `-Purge` (DELETE matching mail, destructive). Its param
allowlist contains ONLY `SearchName` + `Preview`, so `invoke_compliance` rejects `-Export`/`-Purge` before
any HTTP — phase 1 can preview but can neither export nor delete. Export arrives in phase 2 as its own
deliberately-added param + tool; Purge stays off entirely (same stance as `Remove-Mailbox`, D-54).

**The two-layer query model.** "Addresses involved" is two different things and the tools keep them
separate: WHERE to search = `-ExchangeLocation` (a list of mailboxes, or `"All"`); WHO a message is
from/to = KQL inside `-ContentMatchQuery` (`from:` / `to:` / `participants:`). `_content_search.build_kql`
assembles the KQL from `keywords`, `from_address`→`from:`, `to_address`→`to:`, `participants`→`participants:`
(sender OR any recipient), `subject`→`subject:"…"`, `date_from`/`date_to`→`received>=/<=`,
`has_attachment`→`hasattachment:true`; a raw `kql` arg is an escape hatch that bypasses assembly. Embedded
`"` are stripped (KQL has no clean escape). An EMPTY assembled query matches ALL items, so the create tool
refuses it — at least one criterion (or raw kql) is required, so you can't dump a mailbox by omission.

**No accidental tenant-wide scan.** `exo_content_search_create` requires EITHER an explicit `mailboxes`
list OR `all_mailboxes:true`; there is no implicit `"All"` default (the open scoping question from the
build request — resolved to the tighter option). Specific-mailbox searches set
`AllowNotFoundExchangeLocationsEnabled:true` so one bad address/alias doesn't fail the whole creation.

Skills (all `SOURCE=m365`, `ENABLED_BY_DEFAULT=False`):
- `exo_content_search_create` (write, **RISK_LEVEL=high** — reads other people's mail): build KQL + locations,
  `New-ComplianceSearch`, verify by `Get-ComplianceSearch` (never report an unverified write — D-43), then
  `Start-ComplianceSearch` unless `start:false`. Returns name, query, locations, initial status.
- `exo_content_search_status` (read): `Get-ComplianceSearch` for one `name` (or ALL when omitted). Reports
  `Status`, estimated `Items`/`Size` (humanized), the query, the locations, and a per-mailbox breakdown
  parsed from `SuccessResults`. Headline item/size come from real object props, not string parsing.
- `exo_content_search_preview` (write — it can START a preview action): idempotent lifecycle. If a
  `<name>_Preview` action exists and is Completed → parse + return a SAMPLE of matching items (Purview caps
  preview); if it exists but isn't done → "still preparing"; if the search estimate isn't Completed yet →
  "wait"; otherwise `New-ComplianceSearchAction -Preview` to start it. Owner can set `require_approval=false`
  in the Capability Console for unattended re-fetches (non-destructive).

**Parsing is fragile — raw is always included.** The compliance endpoint returns `SuccessResults` (location
stats) and preview `Results` (item rows) as semicolon/comma-delimited strings whose values (subjects) can
themselves contain the delimiters. `_content_search` parses best-effort by anchoring records at `Location:`
and stopping each field at the next KNOWN key, and ALWAYS returns the truncated raw string alongside the
parsed rows so nothing is silently lost. If a future tenant's format drifts, fix the parser here and note it.

**Prerequisite role (ties to the D-autopilot RBAC lesson).** Content Search needs the signed-in admin to be
an **eDiscovery Manager** (or hold the *Compliance Search* / *Preview* roles) in the Purview portal — a
SEPARATE assignment from Exchange/Intune admin. A 401/403 surfaces that hint. DTM signs clients in with
least-privilege accounts and grants roles as needed, so expect to grant this before first use.

Tests: tests/test_exo_content_search_create.py, tests/test_exo_content_search_status.py,
tests/test_exo_content_search_preview.py.

## Amendment (2026-06-30, D-116) — Content Search phase 2: export + server-side download

Phase 2 adds **export with download into the dashboard** (`exo_content_search_export`, write,
RISK=high, default-off). The owner chose backend-pull (Option B) over Microsoft's ClickOnce
eDiscovery Export Tool, because that tool is Windows/legacy-only AND surfacing its SAS export key to
the browser would violate D-1/I-3 (browser never holds vendor secrets).

**Allowlist change (revises D-115's "Export unreachable").** `New-ComplianceSearchAction` param
allowlist widens from `{SearchName, Preview}` to `{SearchName, Preview, Export}`; `-Purge` (the
destructive delete-matching-mail action) stays OFF the list → still structurally unreachable.
`Get-ComplianceSearchAction` gains `IncludeCredential` (to read the export's staging-container SAS
URL). No new endpoint/audience — same `invoke_compliance()`.

**The download path.** A completed `<name>_Export` action stages results in an Azure blob container
behind a short-lived SAS URL. There is no azcopy/az on the box, so `clients/azure_blob.py` reads the
container directly over the Blob REST API using two NEW stdlib helpers in `_http.py`: `http_bytes`
(raw bytes — `http_json` JSON-decodes, useless for the List-Blobs XML) and `download_to_file`
(streams to disk in 1 MB chunks, enforcing a byte cap AS IT GOES so an oversized blob can't fill the
disk; removes the partial file on cap-hit or error). `azure_blob.list_blobs` parses the EnumerationResults
XML (follows NextMarker); `download_container` preserves folder structure, sums bytes, and pre-checks the
declared total against the cap.

**Security invariants for the new egress surface:**
- The SAS is a live credential: it stays server-side, is passed only to the downloader, and is NEVER
  in a tool result or log (test asserts the sig never appears in the result repr).
- Egress is fail-closed to `*.blob.core.windows.net` only (`_check_host`) — not an arbitrary URL.
- Blob names are sanitized (`_safe_rel` drops leading `/` and `..`) so a crafted name can't escape the
  dest dir.
- Total download capped by `MSPAI_CONTENT_EXPORT_MAX_GB` (default 2 GB); over-cap aborts with an honest
  message (raise the cap or narrow the search) — never a silent truncation.

**Delivery (I-8).** Results land under `<vault>/exports/<tenant>/<search>/` (a real deliverable dir, not
`/.tmp/`) and the tool returns a `download_url` = `/api/fs/download?path=…` (the existing admin-gated raw
download). Long downloads stream live progress via `ctx.progress` (D-112). The export is INLINE in the
tool call — fine within the cap; a true background-job path is a later option if huge exports are needed.

**Lifecycle** (idempotent, mirrors preview): no export action + search Completed → start `-Export`;
action InProgress → "still preparing"; action Completed → read credentials → download → return link.
Same eDiscovery-role prerequisite as D-115 (plus the Export role).

**Not verified against a live tenant** — the Blob REST shapes (List XML, SAS concatenation, export
credential labels) are coded defensively (credential parse falls back to scanning for a blob URL + `sv=`
SAS) and unit-tested with stubbed transports, but a first live export should be watched. If labels/format
drift, fix `_content_search.parse_export_credentials` / `azure_blob` and note it here.

Tests: tests/test_azure_blob.py, tests/test_exo_content_search_export.py.
