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
