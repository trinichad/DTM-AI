# SOP — Kaseya VSA connector (read-only REST, D-68)

> Kaseya VSA 9.x on-prem REST API (`/api/v1.0`). The client (`clients/kaseya.py`) is READ-ONLY:
> user/pass → `GET /auth` → bearer token (~20 min), then GET-only. Every Kaseya tool is a read.

## Auth & client
- Basic `base64(user:pass)` → `/api/v1.0/auth` → `Result.Token`; a pre-issued `KASEYA_TOKEN`
  is used directly. Token cached ~20 min, re-minted on expiry. (Verified vs vsa.example.com.)
- Envelope: every endpoint returns `{ TotalRecords, Result: [...]|{}, ResponseCode, Status, Error }`.
  `_kaseya_common.result(client, path)` unwraps `.Result` and surfaces `.Error` as a clean failure.
- `client.get_all(path)` pages via `$top`/`$skip`. Per-agent audit reads take a numeric `AgentId`;
  `_kaseya_common.resolve_agent(client, needle)` maps a machine name OR id to one agent (refuses
  an ambiguous substring with the candidates listed).
- **Per-agent audit path shape (lesson, D-68):** the AgentId goes RIGHT AFTER `/audit/`, not at the
  end — `/assetmgmt/audit/{agentId}/software/installedapplications`, NOT
  `/assetmgmt/audit/software/installedapplications/{agentId}`. The id-at-end form 404s on a machine
  that plainly exists; the id-first form is what the VSA 9.x REST reference documents. (The
  `runnow|schedule` PUTs are the one exception — `/audit/{baseline|latest|sysinfo}/{agentId}/...`.)
- **`"Error": "None"` is SUCCESS, not failure (lesson, 2026-06-19).** VSA 9 success envelopes carry
  the LITERAL STRING `"Error": "None"` next to `ResponseCode: 0` / `Status: "OK"` and a full
  `Result`. So a truthy `Error` field is NOT a failure signal — `_kaseya_common._envelope_error()`
  and the client's `_write()` treat `none`/`null`/empty as success. (This bug was masked while every
  audit path 404'd/403'd; fixing the paths surfaced it — every successful 200 was being turned into
  a false `error: "None"`.)
- **Full REST-path audit vs the live Swagger (2026-06-19).** Every Kaseya tool's endpoints were
  cross-checked against `reference/kaseya-vsa9-rest-api-endpoints.md` and live-probed. Corrections
  applied: alarms `/alarms`→`/assetmgmt/alarms/{returnAllRecords}`; agent-procedure schedule
  `…/scheduled`→`…/scheduledprocs`; local admins `…/localusergroupmembers`→`…/members`; patch scan
  `/patch/{id}/schedule` (deploy!) →`/patch/{id}/scannow`; machine-group delete
  `/system/orgs/{o}/machinegroups/{g}`→`/system/machinegroups/{g}`; plus the audit `{agentId}`-first
  ordering. `run_audit`, org create/update/delete, asset custom fields, close-alarm, machine health,
  and all agent-procedure run/cancel paths were verified correct. NOTE: `kaseya_org_structure`'s
  per-org `…/{oid}/locations` + `…/{oid}/staff` could not be live-verified (0 orgs in this account's
  scope) — re-check when an org is in scope.
- **403 on audit reads is a ROLE-rights gap, not a bad URL (lesson, D-68).** Audit-detail endpoints
  return `HTTP 403 / ResponseCode 4030001 "Access denied"` when the API user's VSA *Role* lacks the
  function access (e.g. Audit) or its *Scope* doesn't cover the machine's group — even though the
  same account can list agents/assets. `result()` now translates 403 into that actionable message.
  Fix is owner-side in the VSA console (System > User Security > Roles / Scopes), not in code.

## Reachable surface (READ_SCOPES['kaseya'])
Bounds the generic `kaseya_read` tool. Dedicated tools call hard-coded read paths on the read-only
client (the tool itself is the review). Prefixes: `/assetmgmt/` (assets, agents, **audit detail**),
`/system/orgs`, `/system/machinegroups`, plus (D-68) `/automation/` (procedures, service desk).
NB: alarms live at `/assetmgmt/alarms/{returnAllRecords}` — covered by `/assetmgmt/`; there is no
bare `/alarms` endpoint (it 404s), and the legacy bare `/audit/` prefix is likewise unused —
Kaseya audit data lives under `/assetmgmt/audit/...`.

## Capabilities
Pre-existing: `kaseya_list_assets`, `kaseya_get_asset`, `kaseya_list_agents`, `kaseya_read`.

D-68 reads (all CATEGORY=read, ENABLED_BY_DEFAULT=True):
- `kaseya_machine_health` — one agent's live record: online/offline, last check-in, **last reboot**,
  OS, IP/gateway/DNS, RAM/CPU, last-logged-in user, agent version (`/assetmgmt/agents/{id}`).
- `kaseya_installed_software` — installed applications + Add/Remove Programs + software licenses
  for a machine (`/assetmgmt/audit/{id}/software/{installedapplications,addremoveprograms,licenses}`).
- `kaseya_security_posture` — detected antivirus/firewall **security products** + local
  administrator accounts on a machine (`/assetmgmt/audit/{id}/software/securityproducts`,
  `.../audit/{id}/members` (local group members), fallback `.../audit/{id}/useraccounts`).
- `kaseya_disk_volumes` — disk volumes + free space per machine
  (`/assetmgmt/audit/{id}/hardware/diskvolumes`).
- `kaseya_audit_summary` — Kaseya's rolled-up audit snapshot for one machine
  (`/assetmgmt/audit/{id}/summary`).
- `kaseya_org_structure` — an org's departments, locations, and staff (no org → list orgs)
  (`/system/orgs`, `/system/orgs/{id}/{departments,locations,departments/{d}/staff}`).
- `kaseya_asset_custom_fields` — custom fields tracked on an asset
  (`/assetmgmt/assets/{id}/customfields`).
- `kaseya_list_alarms` — open alarms across the tenant (`/assetmgmt/alarms/{returnAllRecords}` —
  there is NO bare `/alarms`; filter/limit applied client-side).
- `kaseya_remote_session_history` — who connected to a machine **remotely** (Kaseya Live Connect /
  Remote Control) and WHEN: admin, start + last-active times, session type, IPs. Most recent first
  (`/assetmgmt/logs/{agentId}/remotecontrol`, optional `legacyremotecontrol`). NB: the VSALog
  endpoints are documented as `{agentGuid}` but accept the numeric `AgentId` (verified live), and —
  unlike the audit endpoints — this log reads WITHOUT the Audit role right (returned 200).
- `kaseya_agent_procedures` — a machine's agent-procedure run **history** (did the script run /
  succeed) or what's **scheduled** (`/automation/agentprocs/{id}/{history,scheduledprocs}` — the
  schedule view is `scheduledprocs`, NOT `scheduled`).
- `kaseya_service_desk_tickets` — Service Desk tickets / one ticket + notes (needs the Service
  Desk module licensed) (`/automation/servicedesks`, `/automation/servicedesktickets/{id}`).

## Honest caveats (from the API audit)
- **Patch posture is NOT in REST v1.0** — there is no GET for missing/pending patches (only a
  scan trigger). That data lives in Kaseya's Data Warehouse / OData API — a separate future
  integration. Do not build patch reporting against this connector.
- **Backup status / monitor-set config** are likewise absent from REST v1.0 (OData only).
- Exact path casing for a few audit subpaths is reconstructed from the help reference; each tool
  fails closed with the path it tried if an endpoint 404s on a given VSA build (verify against the
  instance's Swagger at `/api/v1.0/swagger/ui/index`). Tools never null-out a row — unknown shapes
  pass the raw record through (house style).
- Read-only bridge accounts are often denied `/system/*` (403 = scope limit, not auth failure);
  the org-structure tool surfaces that distinctly.

## Amendment (2026-06-13, D-69) — WRITE capability (bounded), read-only no longer absolute

Owner asked for the write surface. The VSA REST v1.0 write set was verified against the Kaseya
help reference. The connector gains writes behind a HARD endpoint allow-list (regex per
method+path), mirroring the EXO cmdlet allowlist — even a promoted AI draft can't reach an
arbitrary write. Two paths:
- `KaseyaClient.write(method, path, body)` — validates (method, path) against `WRITE_RULES`;
  refuses anything else and refuses every destructive rule.
- `KaseyaClient.write_destructive(method, path, body)` — ONLY the structural DELETEs (org,
  machine group, asset). Reachable solely from a hand-written CATEGORY=destructive skill; the
  builder validator blocks AI candidates from referencing `write_destructive` (same floor as EXO
  `invoke_destructive`). Each destructive run carries the un-disableable per-action approval.

WRITE tools (all CATEGORY=write, REQUIRES_APPROVAL=True, ENABLED_BY_DEFAULT=False):
- `kaseya_close_alarm` — close an alarm with a note (`PUT /assetmgmt/alarms/{id}/close`).
- `kaseya_run_procedure` — **run an agent procedure now on a machine** (`PUT
  /automation/agentprocs/{agentId}/{procId}/runnow`). HIGHEST RISK: runs a human-authored
  procedure = effectively code on the endpoint. Procedure resolved by name/id from the catalog.
- `kaseya_cancel_scheduled_procedure` — cancel a scheduled procedure (`DELETE
  /automation/agentprocs/{agentId}/{procId}`).
- `kaseya_run_audit` — run a baseline/latest/sysinfo audit now (`PUT
  /assetmgmt/audit/{type}/{agentId}/runnow`).
- `kaseya_schedule_patch_scan` — run a missing-patch SCAN now (`PUT
  /assetmgmt/patch/{agentId}/scannow`). NB: scan only — `/schedule` (deliberately NOT used) schedules
  patch DEPLOYMENT; REST v1.0 patch *install* otherwise needs an agent procedure.
- `kaseya_set_warranty` — set purchase/warranty-expiry dates on an asset (`PUT
  /assetmgmt/audit/{agentGuid}/hardware/purchaseandwarrantyexpire`).
- `kaseya_add_ticket_note` — add a note to a Service Desk ticket (`POST
  /automation/servicedesktickets/{id}/notes`).
- `kaseya_update_ticket` — set a ticket's status / priority / assignee (name or id resolved
  against the desk; `PUT .../status|priority/{id}`, `.../servicedesks/assign/{tid}/{staffId}`).
- `kaseya_create_org` / `kaseya_update_org` — add/update an organization (`POST /system/orgs`,
  `PUT /system/orgs/{id}`).
- `kaseya_create_machine_group` — add a machine group to an org (`POST
  /system/orgs/{orgId}/machinegroups`).

DESTRUCTIVE tools (CATEGORY=destructive, per-run approval floor, disabled by default):
- `kaseya_delete_asset` — delete one asset record (`DELETE /assetmgmt/assets/{assetId}`).
- `kaseya_delete_machine_group` — delete a machine group (`DELETE /system/machinegroups/{id}` — by
  MG id, NOT org-scoped; create is the org-scoped `POST /system/orgs/{orgId}/machinegroups`).
- `kaseya_delete_org` — **delete a whole client organization** (`DELETE /system/orgs/{id}`).
  Catastrophic; recommend leaving disabled.

NOT in REST v1.0 (do not build): patch INSTALL/deploy, create-ticket, agent rename/move/
suspend/delete, agent deploy/uninstall, remote control, department/staff/role/user CRUD. (Patch
install can only be done by running an agent procedure — which is why run_procedure is the
highest-risk lever.)

## Amendment (2026-06-13, D-70) — owner-authorized command execution on endpoints

The owner (trust anchor, I-5) EXPLICITLY authorized running arbitrary commands on endpoints through
Kaseya — for troubleshooting, installs, and admin tasks (e.g. New-ADUser on a domain controller).
This DELIBERATELY extends Rule #6 ("no LLM-emitted command strings are executed") at the owner's
direction. It is not the agent improvising past a guardrail; it is the owner installing a capability.
Compensating controls (all in code):
- **Per-run human approval is the core control.** `kaseya_run_command` is CATEGORY=write,
  REQUIRES_APPROVAL=True, ENABLED_BY_DEFAULT=False. The agent PROPOSES a command; a human sees the
  EXACT command on the approval card and approves it before it runs — so it's "AI drafts, human
  authorizes each command," closer to the admin terminal's spirit than unattended RCE. Every run is
  audited. WARNING (documented for the owner): turning approval OFF on this tool in the Capability
  Console = unattended arbitrary command execution on client machines — do not. Use batch-approval
  (D-59, approve-once + N repeats) for controlled automation bursts instead.
- **Mechanism.** Kaseya REST cannot take a raw command — it runs PROCEDURES. So the owner creates
  ONE agent procedure in Kaseya (the console; the API can't author procedures) that takes the
  command as a prompt and runs it, capturing output to a custom field. `kaseya_run_command`
  resolves that procedure (name from config `KASEYA_RUN_COMMAND_PROCEDURE`, default
  "MSP AI Run Command") and schedules it to run now with the command as a ScriptPrompt
  (`PUT /automation/agentprocs/{agentId}/{procId}/schedule` — runnow takes no params, schedule
  does). Async: the agent runs it at next check-in (seconds if online).
- **Output read-back.** The procedure writes stdout to a custom field (config
  `KASEYA_COMMAND_OUTPUT_FIELD`, default "AI_Command_Output"); `kaseya_command_output` (read) reads
  it back so results show in chat.

### The Kaseya procedure the owner creates (one-time, in the console)
"MSP AI Run Command": a Script Prompt captioned `command`; a step that runs `#command#` via
PowerShell (capturing the result into a variable); a step that writes that variable to the asset
custom field `AI_Command_Output`. (Exact step names vary by VSA build; the owner authors it. The
Automation Exchange has runnable-command templates to start from.)

## Amendment (2026-06-13, D-71) — Command Toolkit sub-group + named IT tools on the engine

UI: tools can declare `GROUP = "<id>"` to cluster WITHIN their source section (registry ToolInfo.group;
`core/tool_groups.py` GROUP_INFO gives each group a title + markdown setup/how-to panel surfaced in
the Capabilities tab via /api/capabilities `group_info`). The `kaseya_command` group bundles the
run-command engine and everything built on it, with the one-time Kaseya setup shown inline — all
still under the **Kaseya VSA** source section.

Shared engine: `_kaseya_common.run_command(ctx, machine, command)` is the single entry point — it
resolves the owner's "MSP AI Run Command" procedure and schedules it now with the command as a
prompt. `kaseya_run_command` and every named tool below route through it (so one Kaseya procedure
powers all of them). Named tools build a SPECIFIC command from parameters (bounded, not arbitrary):
- `kaseya_install_software` (Chocolatey; friendly name → package map, bootstraps choco if missing)
- `kaseya_network_ping` (Test-Connection; target validated — no shell metacharacters)
- `kaseya_restart_service` (Restart-Service; name validated)
- `kaseya_flush_dns` (ipconfig /flushdns)
- `kaseya_reboot_machine` (shutdown /r /f /t; default 60s delay)
All CATEGORY=write, approval-gated, disabled by default; output read back via kaseya_command_output.
Future named tools (e.g. create-AD-user) drop into this group the same way — set GROUP=kaseya_command,
call _kaseya_common.run_command with the built command.

## Amendment (2026-06-13) — ticketing + warranty tools removed (owner: not used)
MSP AI does not use the Kaseya Service Desk/ticketing module or asset warranty tracking. The
owner removed `kaseya_service_desk_tickets`, `kaseya_add_ticket_note`, `kaseya_update_ticket`,
and `kaseya_set_warranty` (the D-68/D-69 entries above are retained for history but those four
tools are gone). Do not rebuild them. The underlying connector write-allowlist rules for the
service-desk/warranty endpoints remain (harmless, unused) unless we prune them later.

## Amendment (2026-06-13, D-72) — Active Directory tools (run on a DC via the command engine)

AD admin tools that build a SPECIFIC PowerShell AD command and run it through
`_kaseya_common.run_command` on the **domain controller's** Kaseya machine (the DC has the
ActiveDirectory module). UI sub-group `kaseya_ad` ("Active Directory — run on a domain
controller"). All CATEGORY=write, REQUIRES_APPROVAL=True, ENABLED_BY_DEFAULT=False; output via
kaseya_command_output.

INJECTION SAFETY: every embedded value goes through `_kaseya_common.ps_quote()` — a PowerShell
single-quoted string with `'`→`''` doubling, so no `$`/backtick/`;` can break out regardless of
content. Inputs also pass `clean_text()` (no control chars/newlines). Commands are wrapped in
`try {…} catch { 'ERROR: ' + $_.Exception.Message }` so a failure shows up in the output field.

Tools: kaseya_ad_get_user, kaseya_ad_create_user (password generated if not given; returned once
only when generated), kaseya_ad_reset_password, kaseya_ad_unlock_account, kaseya_ad_enable_account
(enable/disable), kaseya_ad_add_group_member, kaseya_ad_remove_group_member.

CAVEATS: (1) target must be a DC / have RSAT AD module. (2) the create/reset password is part of
the command → visible on the approval card + Kaseya command log (inherent to setting an AD
password by command). (3) Cylance Script Control in Block mode can block PowerShell — exclude the
Kaseya agent working dir (e.g. C:\kworking) in the Cylance policy if so (one-time, affects the
whole command engine, not just AD).

## Amendment (2026-06-13, D-72 follow-up) — full AD properties, raw attributes, Entra sync
- `kaseya_ad_create_user` expanded: now accepts the full profile set (title/department/company/
  office/description/office_phone/mobile_phone/fax/home_phone/street/city/state/postal_code/
  country[2-letter]/manager/script_path/display_name/email/upn) via the shared
  `_kaseya_common.AD_PROP_SCHEMA` + `ad_property_fragments()` (same builder used by set_user).
- `kaseya_ad_set_user` (new): modify ANY of those standard properties on an existing user, PLUS
  raw AD attributes for AD/Entra HYBRID changes — `set_attributes` (-Replace, single-valued),
  `add_attributes`/`remove_attributes` (-Add/-Remove, multi-valued e.g. proxyAddresses; value may
  be a list), `clear_attributes` (-Clear). Attribute NAMES validated `^[A-Za-z0-9-]+$`; values
  ps_quote'd via `_kaseya_common.ad_hashtable()` — injection-safe (proven by test).
- `kaseya_entra_delta_sync` (new): `Start-ADSyncSyncCycle -PolicyType Delta|Initial` on the Azure
  AD Connect / sync server's machine — push AD changes to Entra/M365 without waiting for the
  scheduled cycle. Group `kaseya_ad` retitled "Active Directory & Entra hybrid".

## Amendment (2026-06-13, D-74) — generic property-provisioning building blocks

> Client-specific provisioning skills (gated to one tenant, with that client's OU/domain/share
> baked in) live as **local, gitignored** skills per deployment — not in the generic product. The
> reusable primitives below compose the same kind of workflow for any client.

**D-74 — generic (any-client) building blocks** to compose the same kind of workflow for anyone:
- `kaseya_ad_create_group` — create any AD group (scope global/universal/domainlocal, category
  security/distribution, OU, description); idempotent skip-if-exists. (group `kaseya_ad`)
- `kaseya_fs_provision_folders` — create a folder, optionally cloning a template tree's sub-folders
  (folders only); aborts if the target exists. (group `kaseya_fs`)
- `kaseya_fs_set_permissions` — icacls: `grant` map (principal → full/modify/read/readonly/write or
  a raw spec like `(OI)(CI)M`), `remove` list, `disable_inheritance`; each grant REPLACES that
  principal's ACE. (group `kaseya_fs`)
- Nesting/membership reuses the existing `kaseya_ad_add_group_member` (a `-Members` value can be a
  user OR a group). New UI group `kaseya_fs` = "File shares & NTFS permissions".

Injection safety holds throughout: every user value is embedded ONLY inside `ps_quote` single-
quoted literals; derived names are built PowerShell-side; group existence is checked via
`-Identity` in try/catch (never a `-Filter` string); icacls principals/rights are regex-validated
before quoting. Same caveats as the AD tools (RSAT module on the target; Cylance Script-Control
exclusion for the agent working dir if PowerShell is blocked).

**D-75 — `kaseya_fs_get_permissions`.** Read-only ACL report: owner + inheritance-protected flag +
each access entry (identity, allow/deny, rights, inherited?) via Get-Acl. Read-only in effect but
CATEGORY=write/approval-gated because it rides the command engine (schedules a procedure), exactly
like `kaseya_network_ping`. Pair before/after `kaseya_fs_set_permissions` to verify. Group
`kaseya_fs`. Path injected once via ps_quote — injection-safe.

## Amendment (2026-06-13, D-76) — DHCP server tools (new group `kaseya_dhcp`)

Windows DHCP management via the `DhcpServer` PowerShell module on the DHCP server, through the
command engine (D-70). Scopes are keyed by network address (`scope_id`, e.g. 192.168.1.0).
- Reads (read-only in effect; CATEGORY=write/approval like all engine tools): `kaseya_dhcp_list_
  scopes`, `kaseya_dhcp_scope_stats` (Free = available IP count), `kaseya_dhcp_list_leases`,
  `kaseya_dhcp_list_reservations`.
- Writes: `kaseya_dhcp_set_scope` (name/state/lease/range/description — only the fields passed),
  `kaseya_dhcp_add_reservation` / `kaseya_dhcp_remove_reservation`, `kaseya_dhcp_add_exclusion` /
  `kaseya_dhcp_remove_exclusion` (add/remove symmetry).
- Shared validators added to `_kaseya_common`: `is_ipv4()` + `clean_mac()` (normalizes any common
  MAC format to `aa-bb-cc-dd-ee-ff`). IPs/MACs validated, then ps_quote'd — injection-safe.
- CAVEAT: run against the DHCP server's machine (has the DhcpServer module / DHCP role). Same
  Cylance Script-Control caveat as the rest of the command engine.

## Amendment (2026-06-13, D-77/78/79) — Group Policy, DNS, Event Viewer (3 new groups)

All ride the command engine (D-70); reads are CATEGORY=write/approval like the rest (engine-run).

**D-77 — Group Policy (`kaseya_gpo`).** `kaseya_gpo_list` (Get-GPO -All, DC), `kaseya_gpo_result`
(gpresult /r on the target machine), `kaseya_gpo_update` (gpupdate /target:computer /force on the
target), `kaseya_gpo_link` / `kaseya_gpo_unlink` (New-/Remove-GPLink at an OU — matching pair, DC),
`kaseya_gpo_backup` (Backup-GPO, DC).

**D-78 — DNS (`kaseya_dns`).** `kaseya_dns_list_zones`, `kaseya_dns_list_records` (filter by
type/name), `kaseya_dns_add_record` / `kaseya_dns_remove_record` (A/AAAA/CNAME/MX/TXT/PTR — matching
pair; MX needs priority), `kaseya_dns_resolve` (Resolve-DnsName FROM any machine — client-side
troubleshooting), `kaseya_dns_clear_cache` (Clear-DnsServerCache; client cache = existing
kaseya_flush_dns). Zone/name/host regex-validated, data ps_quote'd.

**D-79 — Event Viewer (`kaseya_events`).** `kaseya_event_query` — Get-WinEvent with a
FilterHashtable built from validated params (log, level→numeric, since_hours→StartTime, event_id,
provider, count≤500); "no events found" is reported cleanly, not as an error.

Targets: GPO link/unlink/backup/list on a DC; gpresult/gpupdate/dns_resolve/event_query on the
endpoint; DNS zone/record/cache on the DNS server. Same Cylance Script-Control caveat.

**D-77 follow-up (2026-06-13) — create + EDIT GPO settings.** Owner asked if policies can be set,
not just linked. Added: `kaseya_gpo_create` (New-GPO), `kaseya_gpo_set_registry` (Set-GPRegistryValue
— registry-backed Administrative-Template values: key under HKLM\/HKCU\, value_name, type
dword/qword/string/expandstring/multistring, value/values), `kaseya_gpo_remove_registry`
(Remove-GPRegistryValue — single value or whole key; matches set). SCOPE NOTE captured in the tool
descriptions + group how_to: only registry-backed settings are cmdlet-settable; UI-only policies
(password/account, user-rights, scripts, software install) require GPMC/secedit and are out of
scope. Key regex-validated (must be HKLM/HKCU), values typed + ps_quote'd — injection-safe.

## Amendment (2026-06-13, D-80) — Windows Registry tools (new group `kaseya_registry`)

Direct machine-registry read/edit via the command engine (distinct from GPO registry settings).
- `kaseya_registry_get` (read a value, or list values+subkeys of a key), `kaseya_registry_set`
  (New-ItemProperty, creates the key path if missing; dword/qword/string/expandstring/multistring),
  `kaseya_registry_delete_value` (Remove-ItemProperty), `kaseya_registry_delete_key` (Remove-Item
  -Recurse — CATEGORY=**destructive**, so always per-action approval + never batch-approvable; a
  bare hive root is refused).
- Shared helpers in `_kaseya_common`: `reg_path()` normalizes HIVE\path → `Registry::HKEY_…\…`
  (works for all 5 hives without a PSDrive), `reg_type_value()` renders a typed -Value. Full path
  ps_quote'd → injection-safe (proven by test).
- CAVEAT: the agent runs as SYSTEM — HKLM is machine-wide, HKCU is SYSTEM's profile (not the
  logged-in user's). Prefer the Group Policy tools when a setting belongs in a GPO.

## Amendment (2026-06-13, D-81) — troubleshooting packs (Diagnostics / Remediation / Network / Update)

21 tools on the command engine; work on servers AND workstations (all use built-in Windows/PS).
- **Diagnostics** (`kaseya_diag`, read-only): processes (top CPU/mem), services (stopped auto-start),
  system (uptime + pending-reboot), network (ipconfig + connections), disk (volumes + biggest
  folders), certs (expiring), scheduled_tasks (non-Microsoft + last result), sessions (quser).
- **Remediation** (joins `kaseya_command`): kill_process (name/PID), service_control (start/stop/
  restart + startup type), clear_print_spooler, disk_cleanup (temp/recycle/WU cache, reports freed),
  repair_system (DISM + sfc), reset_network (winsock+ip reset, reboot after), renew_ip, uninstall_
  software (choco then PackageManagement).
- **Network** (`kaseya_net`): port_test (Test-NetConnection), traceroute (tracert), adapter
  (list/enable/disable/restart — disabling a remote NIC can self-isolate, RISK high).
- **Windows Update** (`kaseya_update`): check (pending + recent via Microsoft.Update COM API, no
  module) and install (download+install pending; never auto-reboots, reports RebootRequired).
Inputs validated (process/service/host/adapter regex, ports/days/hops as ints) then ps_quote'd.

## Amendment (2026-06-13, D-85) — UniFi field tools via Kaseya (group `kaseya_unifi`)

On-site UniFi work the controller API CAN'T do because the device isn't adopted/reachable yet —
run from a Windows Kaseya agent ON the client's LAN through the command engine. SSH uses **plink**
(PuTTY; auto-installed via choco if missing) since Windows' built-in ssh can't take a password
non-interactively. Shared helper `_kaseya_common.ssh_command_ps(user, ip, password, remote_cmd)`
builds an injection-safe (ps_quote'd) plink invocation; host key auto-cached by piping "y".
- `kaseya_unifi_scan` — ping-sweep the machine's /24 (async .NET pings) + ARP table matched against
  Ubiquiti OUI prefixes → device IP/MAC list. RISK low.
- `kaseya_unifi_set_inform` — SSH `set-inform http://<controller>:<port>/inform` (default 8080,
  creds default ubnt/ubnt). RISK medium. Re-run after adopting.
- `kaseya_unifi_factory_reset` — SSH `set-default`. CATEGORY=destructive (always-approval).
CAVEAT: the SSH password is part of the command → visible on the approval card + Kaseya log
(inherent to non-interactive SSH). Adopted devices use the controller-configured SSH creds, not
ubnt/ubnt. Complements the first-class `unifi` controller connector (D-84).
