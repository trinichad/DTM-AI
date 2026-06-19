# Kaseya VSA 9 — REST API v1.0 endpoint catalog (reference)

> **Source:** the live Swagger on the tenant — `https://vsa.example.com/api/v1.0/swagger/ui/index`
> **Base URL:** `https://vsa.example.com/api/v1.0`
> **Auth:** `Authorization: Bearer {token}` (token from `GET /auth` with Basic `base64(user:pass)`).
> **Standard envelope:** `{ "TotalRecords": n, "Result": [...], "ResponseCode": 0, "Status": "...", "Error": "..." }`
>
> **What this is:** every operation exposed by the VSA 9 REST API, grouped by Swagger tag. This is the
> API the MSP AI Kaseya client calls. Today we only use a handful (`/assetmgmt/assets`,
> `/assetmgmt/agents`, some `/assetmgmt/audit/...`); this catalog documents the rest so the assistant
> knows what's *possible* and so we can pick new **read** tools to build.
>
> ⚠️ **STATUS IN MSP AI:** REFERENCE ONLY. The agent reaches Kaseya only through registered tools +
> the scoped `kaseya_read` connector (GET-only, allow-listed paths). Nothing here is auto-callable.
> 🟢 **GET = read** (safe; candidates for new read tools). 🔴 **POST / PUT / DELETE / PATCH = write/
> action** — future only, behind the Capability Console + approval gate (CLAUDE.md Rule #1). A few GETs
> are sensitive (e.g. `/assetmgmt/audit/{id}/credentials`, ITGlue passwords) — treat as restricted.
>
> Search keywords: kaseya vsa9 rest api endpoint read patch eventlog audit installed applications disk
> services alarms tickets org machine group user agent procedure backup status monitoring logs.

---

## ⭐ High-value READ endpoints we do NOT yet expose (build-tool candidates)
These are GETs that would meaningfully extend what the assistant can answer, today, read-only:
- `GET /assetmgmt/audit/{agentId}/software/installedapplications` — installed apps per machine
- `GET /assetmgmt/audit/{agentId}/software/securityproducts` — AV/security products present
- `GET /assetmgmt/audit/{agentId}/hardware/diskvolumes` (+ `/hardware/diskvolumes/all`) — disk/volume info
- `GET /assetmgmt/audit/{agentId}/summary` — machine audit summary
- `GET /assetmgmt/patch/{agentId}/status` · `/history` · `/machineupdate/{hideDeniedPatches}` — patch posture
- `GET /assetmgmt/alarms/{returnAllRecords}` · `/alarms/{alarmId}` — open alarms
- `GET /assetmgmt/logs/{agentId}/eventlog/{application|system|security|...}` — Windows event logs
- `GET /assetmgmt/audit/{agentId}/useraccounts` · `/groups` · `/members` · `/credentials`¹ — local accounts/groups
- `GET /kcb/servers|workstations|virtualmachines` · `GET /kcb/status/{orgId}` — Kaseya backup status
- `GET /automation/agentprocs` · `/automation/agentprocs/{agentId}/history` — agent procedures & run history
- `GET /system/orgs` · `/system/orgs/{orgId}/machinegroups` · `/system/machinegroups` — org/group structure
- `GET /automation/tickets` · `/automation/servicedesks/...` — ticketing
- `GET /assetmgmt/agents/uptime/{since}` — agent uptime

¹ `/credentials` returns stored machine credentials — sensitive; gate carefully even as a read.

---

## AgentInfo
- 🟢 GET `/kaseyaone/agentinfo/{orgId}`

## AgentProcedure
- 🟢 GET `/automation/agentprocs` — list agent procedures
- 🟢 GET `/automation/agentprocs/prompts`
- 🟢 GET `/automation/agentprocsportal`
- 🟢 GET `/automation/agentprocs/{agentId}/scheduledprocs`
- 🟢 GET `/automation/agentprocs/{agentProcId}/prompts`
- 🔴 PUT `/automation/agentprocs/{agentId}/{agentProcId}/runnow` — run a procedure on a machine
- 🔴 PUT `/automation/agentprocs/{agentId}/{agentProcId}/runnowgetid`
- 🔴 PUT `/automation/agentprocs/{agentId}/{agentProcId}/schedule`
- 🔴 DELETE `/automation/agentprocs/{agentId}/{agentProcId}`
- 🟢 GET `/automation/agentprocs/{agentId}/history`
- 🟢 GET `/automation/variables`
- 🟢 GET `/automation/agentprocs/proclist`
- 🟢 GET `/automation/agentprocs/proclist/history`
- 🟢 GET `/automation/agentprocs/proclist/execution/history`

## AgentProcedureEvent
- 🔴 POST `/system/agentProcedureEvent/{save|remove|move|rename|saveFolder|removeFolder|moveFolder|renameFolder}`

## Alarm
- 🟢 GET `/assetmgmt/alarms/{returnAllRecords}`
- 🟢 GET `/assetmgmt/alarms/{alarmId}`
- 🔴 PUT `/assetmgmt/alarms/{alarmId}/close`

## Alert
- 🔴 POST `/automation/agentalerts/{alertId}`
- 🔴 POST `/automation/systemalerts/{alertId}`
- 🔴 POST `/automation/getalerttracking/{alertTrackingId}`
- 🟢 GET `/automation/alertdefinitions`
- 🔴 PUT `/automation/alertdefinitions`
- 🔴 POST `/automation/alertdefinitions`
- 🔴 DELETE `/automation/alertdefinitions`

## Asset
- 🟢 GET `/assetmgmt/assets` · `/assetmgmt/assetsx` — asset-management records (we use `assets`)
- 🟢 GET `/assetmgmt/agents` · `/assetmgmt/agentsx` — managed agents (machine-group view; we use `agents`)
- 🟢 GET `/assetmgmt/agentactiveadmins`
- 🟢 GET `/assetmgmt/agentportalaccess`
- 🟢 GET `/assetmgmt/agentsonnetwork/{networkId}`
- 🟢 GET `/assetmgmt/temporaryagents`
- 🟢 GET `/assetmgmt/agentsinview/{viewId}`
- 🟢 GET `/assetmgmt/connectiongatewayips`
- 🟢 GET `/assetmgmt/assettypes`
- 🟢 GET `/assetmgmt/assets/{assetId}` · `/assets/getassetbyid/{assetId}`
- 🔴 DELETE `/assetmgmt/assets/{assetId}`
- 🟢 GET `/assetmgmt/assets/{assetId}/agentaudit`
- 🟢 GET `/assetmgmt/assets/rcmachines` · `/assets/rcmachines/{viewId}` — remote-control machines
- 🔴 POST `/assetmgmt/assets/rcservice`  ·  🔴 DELETE `/assets/deletercservice`  ·  🔴 PUT `/assets/updatercservice`
- 🟢 GET `/assetmgmt/assets/{assetId}/getrcservices` · `/assets/getrcservices`
- 🔴 PUT `/assetmgmt/assets/{assetId}/setproxy` · `/{assetId}/assignservice`
- 🟢 GET `/assetmgmt/agents/{agentId}`
- 🔴 PUT `/assetmgmt/agents/{agentId}/settings/tempdir`
- 🟢 GET `/assetmgmt/agent/{agentId}/settings` · `/settings/userprofile` · `/agent/settings/userprofiles`
- 🔴 PUT `/assetmgmt/agent/{agentId}/settings/userprofile` · `/KLCAuditLogEntry` · `/settings/checkincontrol`
- 🔴 DELETE `/assetmgmt/agents/{agentId}/{uninstallFirst}` — uninstall/remove an agent
- 🟢 GET `/assetmgmt/assets/customfields` · `/assets/{agentId}/customfields`
- 🔴 POST/PUT/DELETE `/assetmgmt/assets/customfields[/{fieldName}]` · 🔴 PUT `/assets/{agentId}/customfields/{fieldName}`
- 🟢 GET `/assetmgmt/agents/packages` · `/agents/uptime/{since}`
- 🔴 POST `/assetmgmt/agents/packages` · 🔴 DELETE `/agents/packages/{packageId}`
- 🔴 PUT `/assetmgmt/agents/{agentId}/rename/{newName}`
- 🔴 POST `/assetmgmt/assets/{networkId}/publishdevice`
- 🟢 GET `/assetmgmt/agent/notes` · 🔴 POST `/agent/{agentID}/note` · 🔴 PUT `/agent/notes` · 🔴 DELETE `/agent/note/{noteID}`
- 🔴 POST `/assetmgmt/agent/upgrade/{agentID}` · 🟢 GET `/agent/schedule/update/{agentID}`
- 🔴 POST `/assetmgmt/device/{deviceId}/promoteToAsset` · `/asset/{assetId}/demoteToDevice`
- 🔴 PUT `/assetmgmt/agent/suspend`

## Audit
- 🟢 GET `/assetmgmt/audit`
- 🔴 PUT `/assetmgmt/audit/{baseline|latest|sysinfo}/{agentId}/{runnow|schedule}` — trigger/schedule audits
- 🟢 GET `/assetmgmt/audit/{agentId}/software/installedapplications`
- 🟢 GET `/assetmgmt/audit/{agentId}/software/securityproducts`
- 🟢 GET `/assetmgmt/audit/{agentId}/hardware/{pcianddisk|printers|diskvolumes|diskpartitions|diskshares}`
- 🟢 GET `/assetmgmt/audit/hardware/diskvolumes/all`
- 🟢 GET `/assetmgmt/audit/{agentId}/summary`
- 🟢 GET `/assetmgmt/audit/{agentGuid}/hardware/purchaseandwarrantyexpire` · 🔴 PUT (same)
- 🟢 GET `/assetmgmt/audit/productsupportlink`

## Auth
- 🟢 GET `/auth` · `/authx` — obtain bearer token (Basic → token)

## AuthAnvil
- 🔴 PUT/DELETE `/kaseyaone/authanvil/ssocertificate`

## BmsiAssetMaps / BmsIntegration / BmsiOrgMaps / BmsiOrgStates / BmsiTicRequestMaps  (BMS/Autotask integration)
- 🟢 GET `/bmsi/config` · `/bmsi/orgstates`
- 🟢 GET `/bmsi/assetmaps[/{assetId}|/bybassetId/{bassetId}|/assetdetail/{assetId}]` · 🔴 PUT `/bmsi/assetmaps/{assetId}/{bassetId}` · 🔴 PUT `/bmsi/assetmaps/jobcomplete/{create|update|deactivate}`
- 🟢 GET `/bmsi/orgmaps[/{orgId}|/bybaccountId/{baccountId}]` · 🔴 PUT `/bmsi/orgmaps/{orgId}/{baccountId}`
- 🟢 GET `/bmsi/ticrequestmaps[/{ticRequestId}|/bybTicketId/{bTicketId}]` · 🔴 PUT `/ticrequestmaps/{ticRequestId}/closed` · 🔴 DELETE `/ticrequestmaps/{ticRequestId}/delete/{bTicketId}` · 🔴 PUT `/ticrequestmaps/jobcomplete/{create|duplicate|resolve}`

## Cluster
- 🔴 POST `/system/cluster/{create|connect|disconnect|setAgentProceduresSyncFolder|setMonitorSetsSyncFolder|setEventSetSync|setPolicySyncFolder}`
- 🟢 GET `/system/cluster/{servers|agentProceduresFolders|monitorSetsFolders|eventSets|policyFolders}`

## Core
- 🟢 GET `/tenant` · `/functions` · `/functions/{moduleId}` · `/ismoduleinstalled/{moduleId}` · `/ismoduleactivated/{moduleId}` · `/environment`
- 🔴 POST `/notification` · `/email`

## Department
- 🟢 GET `/system/orgs/{orgId}/departments` · `/system/departments/{deptId}`
- 🔴 POST `/system/orgs/{orgId}/departments` · 🔴 PUT/DELETE `/system/departments/{departmentId}`

## DeployPageCustomization
- 🟢 GET `/agent/{partitionId}/deploypagecustomization`

## Documents  (files stored on agents)
- 🟢 GET `/assetmgmt/documents/{agentId}/folder/{path}` · `/file/{path}` · `/getfiles/{agentId}/folder|file/{path}`
- 🔴 PUT `/assetmgmt/documents/{agentId}/file/{path}` · `/folder/{folders}` · `/file/Move/{source}/{destination}`
- 🔴 DELETE `/assetmgmt/documents/{agentId}/{path}` · `/Rename/{source}/{destination}` · `/getfiles/{agentId}/{path}`
- 🟢 GET `/assetmgmt/documents/{allservicesaudits|distinctservicenames|allvolumelabels|distinctvolumelabels}`
- 🟢 GET / 🔴 PUT / 🔴 DELETE `/assetmgmt/customextensions/{agentId}/folder|file|endpointref/{path}`

## Echo
- 🟢 GET `/echo` · `/echoauth` · `/echossl` — connectivity tests

## Endpoint (storage)
- 🟢 GET `/storage/endpoint/spaceavailable/{folderType}/{fileType}/{bytes}`
- 🔴 POST `/storage/endpoint/file/{folderType}/{fileType}/{filename}`

## Environment
- 🔴 POST `/sample/schedule`

## EventLog
- 🟢 GET `/assetmgmt/logs/{agentId}/eventlog/{application|directoryservice|system|security|dnsserver|internetexplorer}`

## EventSetAction / EventSetSyncServer
- 🔴 POST `/system/eventSetAction/{save|delete|create|rename}`
- 🔴 POST `/system/syncServer/{exportEventSets|syncEventSets|exportEventSet|importEventSet|deleteEventSet|createEventSet|renameEventSet|validateEventSet*}`

## File
- 🟢 GET `/storage/file/{fileId}/contents`

## GenericTicketing
- 🟢 GET `/automation/genericticketing/{agentId}/tickets` · `/{ticketId}` · `/{ticketId}/attachments/{attachmentKey}` · `/ticketfieldsvisibility` · `/statuslist` · `/prioritylist` · `/categories` · `/severities` · `/ticketingmodulename`
- 🔴 PUT `/automation/genericticketing/{ticketId}` · 🔴 POST `/{ticketId}/notes` · `/{agentId}/ticket` · `/attachmenturi/{ticketId}`

## InfoCenter
- 🟢 GET `/infocenter/messages` · 🔴 PUT `/messages/{isRead}` · 🔴 DELETE `/messages/{messageID}`

## ITGlue  (documentation integration — passwords/SSL/domains; sensitive)
- 🟢 GET `/itglue/configurations/bymachineguid/{machineGuid}` · `/vsa/config` · `/organizations/{orgId}/alert` · `/vsa/sync[/{agentGuid}]` · `/accounts/{machineGuid}` · `/accountslist/{machineGuid}` · `/passwords/{id}`¹ · `/contacts/{id}` · `/users/{id}` · `/locations/{id}` · `/ssl_certificates/{id}` · `/domains/{id}` · `/configuration/{id}`
- 🔴 PATCH `/itglue/configurations/{configId}`
  ¹ `/itglue/passwords/{id}` returns secrets — restricted even as a read.

## LastBackupStatus  (Kaseya Backup / KCB)
- 🟢 GET `/kcb/servers` · `/kcb/workstations` · `/kcb/virtualmachines` · `/kcb/status/{orgId}`

## Machine  (sample module)
- 🟢 GET `/sample/machines` · `/sample/machines/{machineId}` · `/sample/machines/{machineId}/procedures`

## MachineGroup
- 🟢 GET `/system/orgs/{orgId}/machinegroups` · `/system/machinegroups` · `/system/machinegroups/{machineGroupId}`
- 🔴 POST `/system/orgs/{orgId}/machinegroups` · 🔴 PUT/DELETE `/system/machinegroups/{machineGroupId}` · 🔴 PUT `/system/scopes/{scopeId}/machinegroups/{machineGroupId}`

## MachineNotifyPolicy
- 🟢 GET `/remotecontrol/notifypolicy/{agentId}`

## MonitorSetEvent
- 🔴 POST `/system/monitorSetEvent/{save|remove|move|update|saveFolder|removeFolder|renameFolder|moveFolder|copyFolder}`

## MultiTenant  (tenant management)
- 🟢 GET `/tenantmanagement/{tenants|tenant/{tenantId}|roletypes|roletypes/{roleTypeId}|licensing/modules/{tenantId}|licensing/module/{moduleId}|settings/defaultsetting/{settingId}|settings/logonpolicy}`
- 🔴 POST/PUT/DELETE `/tenantmanagement/tenant[...]` · `/tenant/modules/{tenantId}` · `/tenant/roletypes/{tenantId}` · `/roletypes` (create/update/delete tenants, modules, role types)

## OAuth
- 🔴 POST `/{tenant}/oauth/token`

## Org
- 🟢 GET `/system/orgs` · `/system/orgs/{orgId}` · `/system/orgs/locations` · `/system/orgs/types`
- 🔴 POST `/system/orgs` · `/system/orgs/networksbyorg` · 🔴 PUT/DELETE `/system/orgs/{orgId}` · 🔴 PUT `/system/scopes/{scopeId}/orgs/{orgId}`

## PatchManagement
- 🟢 GET `/assetmgmt/patch/{agentId}/status` · `/{agentId}/history` · `/{agentId}/machineupdate/{hideDeniedPatches}`
- 🔴 PUT `/assetmgmt/patch/{agentId}/scannow` · `/patch/runnow` · `/{agentId}/schedule` · `/{agentId}/setignore`
- 🔴 DELETE `/assetmgmt/patch` · `/{agentId}/cancelschedule` · `/{agentId}/{patchId}/setignore`

## PolicyEvent / PolicyManagement / PolicySettings / PolicySyncServer
- 🟢 GET `/policy` · `/policy/{policyId}` · `/remotecontrol/policysetting/{agentId}/oneclickaccess`
- 🔴 POST `/system/policyEvent/{save|saveNew|saveFolder|remove|removeFolder|rename|renameFolder|move|moveFolder}`
- 🔴 POST `/system/syncServer/{export|import|save|remove|rename|move|validate|check}Policy[Folder]...` (policy sync set)

## QuickLaunch
- 🟢 GET `/automation/agentprocs/quicklaunch` · `/quicklaunch/askbeforeexecuting`
- 🔴 PUT `/automation/agentprocs/quicklaunch/askbeforeexecuting/{value}` · `/agentProcs/quicklaunch/{agentProcId}` · 🔴 DELETE `/agentProcs/quicklaunch/{agentProcId}`

## QuickView  (per-machine audit details)
- 🟢 GET `/assetmgmt/audit/{agentId}/{credentials¹|software/startupapps|members|software/addremoveprograms|groups|software/licenses|useraccounts}`
  ¹ credentials = sensitive.

## ReferencedAgentInfo
- 🟢 GET `/agents/{agentId}/referencedagents`

## Rest  (third-party app integration)
- 🟢 GET `/thirdpartyapps/status` · `/{tenantId}/status` · `/notification/{appId}[/{messageId}]`
- 🔴 PUT `/thirdpartyapps/{tenantId}/status/{enable}` · 🔴 POST `/notification` · 🔴 DELETE `/notification/{appId}/{messageId}`

## Role
- 🟢 GET `/system/roles` · `/system/roles/{roleId}` · `/system/roletypes` · `/system/roletypes/{roleTypeId}`
- 🔴 POST `/system/roles` · 🔴 DELETE `/system/roles/{roleId}` · 🔴 PUT `/system/roles/{roleId}/users/{userId}`

## Scope
- 🟢 GET `/system/scopes` · `/system/scopes/{scopeId}`
- 🔴 POST `/system/scopes` · 🔴 DELETE `/system/scopes/{scopeId}` · 🔴 PUT `/system/scopes/{scopeId}/users/{userId}`

## ServerManagement
- 🟢 GET `/system/whitelistedfiletypes`

## ServiceDesk
- 🟢 GET `/automation/servicedesks` · `/{serviceDeskId}/{tickets|status|priorities|categories|customfields}` · `/servicedesktickets/{ticketId}[/notes|/customfields/{id}]` · `/servicedesktickets/{incidentNumber}`
- 🔴 POST `/automation/servicedesktickets/{ticketId}/notes` · `/{serviceDeskId}/ticket`
- 🔴 PUT `/automation/servicedesktickets/{ticketId}/{customfields/{id}|priority/{priorityId}|status/{statusId}}` · `/servicedesks/assign/{serviceDeskTicketId}/{staffId}`

## Staff
- 🟢 GET `/system/staff` · `/staff/{staffId}` · `/orgs/{orgId}/staff` · `/departments/{deptId}/staff`
- 🔴 PUT/DELETE `/system/staff/{staffId}` · 🔴 POST `/system/departments/{deptId}/staff`

## SyncServer  (master/sync of agent procedures & monitor sets — all 🔴 POST)
- 🟢 GET `/system/syncServer/echo`
- 🔴 POST `/system/syncServer/{export|import|remove|rename|move|validate|check}AgentProcedure[Folder[s]]...`
- 🔴 POST `/system/syncServer/{export|import|remove|move|update|save|rename|validate}MonitorSet[Folder[s]]...`

## SystemLog
- 🟢 GET `/system/logs`

## TemporaryAgent
- 🟢 GET `/temporaryagent/config` · `/agentpackages`
- 🔴 POST `/temporaryagent` · `/temporaryagent/{agentGuid}/notes` · `/temporaryagent/email` · `/agent/packagelink`
- 🔴 PUT `/temporaryagent/{agentGuid}/name` · 🔴 DELETE `/temporaryagent/{agentGuid}`

## Ticketing
- 🟢 GET `/automation/tickets` · `/tickets/{ticketId}` · `/tickets/{ticketId}/notes` · `/tickets/fromrequest/{ticketRequestId}`
- 🔴 POST `/automation/tickets` · 🔴 PUT `/automation/tickets/{ticketId}`

## UpgradeMessage
- 🟢 GET `/system/upgradeMessage/get` · 🔴 POST `/system/upgradeMessage/snooze`

## User
- 🟢 GET `/system/users` · `/users/{userId}` · `/system/user/uimode` · `/system/currentuser`
- 🔴 POST `/system/users` · `/users/{username}/passwordreset` · 🔴 PUT `/users/{userId}` · `/{userId}/enable` · `/{userId}/disable` · `/{userId}/password/{adminPassword}/update` · 🔴 DELETE `/users/{userId}` · `/users/session`

## View
- 🟢 GET `/system/views`

## VSALog  (per-agent logs)
- 🟢 GET `/assetmgmt/logs/{agentguid}/{agent|networkstats|alarms|configurationchanges|monitoractions|legacyremotecontrol|remotecontrol|agentprocedure|liveview}`
- 🟢 GET `/assetmgmt/logs/{agentId}/logmonitoring`

---

*Catalog captured from the live VSA9 Swagger on 2026-06-03. Path params shown in `{braces}`. When Kaseya
upgrades, re-open the Swagger UI and refresh this file. Pair with
[Kaseya agent-procedure STEP commands](kaseya-vsa9-agent-procedure-commands.md) (the on-machine action verbs).*
