# Kaseya VSA 9 — modules & features (reference)

> **Source:** Kaseya VSA 9 help (`https://help.vsa9.kaseya.com/help/Content/VSA/<id>.htm`).
> **What this is:** conceptual reference for VSA *modules/features* — what each one does and what data
> it makes available — paired with the REST endpoints that expose it (see the companion
> [REST API endpoint catalog](kaseya-vsa9-rest-api-endpoints.md) and the
> [agent-procedure command reference](kaseya-vsa9-agent-procedure-commands.md)).
>
> ⚠️ **STATUS IN MSP AI:** REFERENCE ONLY. Helps the assistant understand what Kaseya can report so it
> answers capability questions and so we can scope new **read** tools. MSP AI v1 is read-only.
>
> Search keywords: kaseya vsa9 module feature audit inventory hardware software system info disk
> baseline latest collect data view group individual machine summary installed applications licenses.

---

## Audit  (chapter: [614](https://help.vsa9.kaseya.com/help/Content/VSA/614.htm))
**What it does:** automatically collects and compares the **hardware and software configuration** of
managed machines. It's the inventory engine behind most "what's on this machine / what changed"
questions. Three audit types are maintained per machine:
- **Baseline audit** — the machine's original/reference state.
- **Latest audit** — the most recent scan (compared to baseline to detect configuration *changes*,
  which can raise change alerts).
- **System Info** — DMI/SMBIOS data (the "40+ details" — make, model, serial number, motherboard, etc.).

**Data categories collected** (Audit Overview, [290](https://help.vsa9.kaseya.com/help/Content/VSA/290.htm)):
1. **Hardware** — CPUs, RAM, PCI cards, disk drives
2. **Installed software** — applications w/ versions, file paths, descriptions, licenses
3. **System information** — DMI/SMBIOS: make, model, serial number, motherboard, etc.
4. **Operating system** — version + service pack/build
5. **Network configuration** — local IP, gateway, DNS, WINS, DHCP, MAC address
6. **Add/Remove Programs** — from Control Panel
7. **Software licenses** — detected vendor license codes

**Per-machine data views** (View Individual Data, [41304](https://help.vsa9.kaseya.com/help/Content/VSA/41304.htm)):
Machine Summary · System Information · Installed Applications · Add/Remove · Software Licenses · Documents.
Also: View Group Data ([41303](https://help.vsa9.kaseya.com/help/Content/VSA/41303.htm)) rolls these up
across a machine group; Collect Data ([41302](https://help.vsa9.kaseya.com/help/Content/VSA/41302.htm))
schedules the baseline/latest/sysinfo scans; Asset ([41301](https://help.vsa9.kaseya.com/help/Content/VSA/41301.htm)).

**REST endpoints that expose Audit data** (read = 🟢; see catalog `Audit`/`QuickView` tags):
- 🟢 `GET /assetmgmt/audit/{agentId}/summary` — machine audit summary
- 🟢 `GET /assetmgmt/audit/{agentId}/software/installedapplications` — installed apps
- 🟢 `GET /assetmgmt/audit/{agentId}/software/securityproducts` — AV/security products
- 🟢 `GET /assetmgmt/audit/{agentId}/software/addremoveprograms` · `/software/licenses` · `/software/startupapps`
- 🟢 `GET /assetmgmt/audit/{agentId}/hardware/{pcianddisk|printers|diskvolumes|diskpartitions|diskshares}`
- 🟢 `GET /assetmgmt/audit/hardware/diskvolumes/all` — disk volumes across all machines
- 🟢 `GET /assetmgmt/audit/{agentId}/{useraccounts|groups|members|credentials¹}` — local accounts/groups
- 🟢 `GET /assetmgmt/audit/{agentGuid}/hardware/purchaseandwarrantyexpire` — purchase/warranty
- 🔴 `PUT /assetmgmt/audit/{baseline|latest|sysinfo}/{agentId}/{runnow|schedule}` — trigger/schedule a scan (write)

¹ `/credentials` returns stored machine credentials — sensitive; gate carefully even as a read.

**MSP AI mapping:** today we expose only `kaseya_list_assets` / `kaseya_list_agents` + the scoped
`kaseya_read` connector. The Audit reads above are prime candidates for new read tools (installed apps,
disk, system info, software licenses) — all GET, all read-only.

---

## Monitor  (chapter: [2180](https://help.vsa9.kaseya.com/help/Content/VSA/2180.htm))
**What it does:** real-time health monitoring of managed machines *and* non-agent devices, with
immediate notifications. When a condition matches, it can fire **ATSE** actions: **A**=create alarm,
**T**=create ticket, **S**=run an agent procedure, **E**=email recipients.

**Six monitoring methods** (Monitor Overview, [2181](https://help.vsa9.kaseya.com/help/Content/VSA/2181.htm)):
1. **Alerts** — typed events on agent machines (offline, RC disabled, software install/removal, hardware
   change, low disk, policy violation, failed procedure, unauthorized access, new device, …)
2. **Event Log Alerts** — watch Windows event logs on agent machines
3. **Monitor Sets** — performance state on agent machines (counters/objects, thresholds)
4. **SNMP Sets** — performance on non-agent devices via SNMP
5. **System Check** — events on non-agent machines (e.g. reachability/port checks)
6. **Log Monitoring** — watch external log files

**Key terms** (Terms & Concepts, [4069](https://help.vsa9.kaseya.com/help/Content/VSA/4069.htm)):
- **Alert** — condition match on a machine/device. **Alarm** — the red/green notification raised by an
  alert. **Alarm Log** — record of alarms; **Monitor Action Log** — record of *all* alerts (alarm or not).
- **Event-based alerts** (discrete occurrences): Alerts, Event Log Alerts, System Checks, Log Monitoring.
  **State-based alerts** (in/out of range): Monitor Sets, SNMP Sets.
- **Counter / Counter Object** — perf metric a Monitor Set watches. **Event Set** — set of event-log
  criteria. **Dashboard / Dashlet** — configurable monitoring views/panes. **Group Alarm** — related
  alarms grouped by category. **Suspend Alarms** — pause alarming for a window without stopping collection.

**REST endpoints that expose Monitor data** (see catalog `Alarm`/`Alert`/`EventLog`/`VSALog`/`MonitorSetEvent`):
- 🟢 `GET /assetmgmt/alarms/{returnAllRecords}` · `/alarms/{alarmId}` — open alarms · 🔴 `PUT /alarms/{alarmId}/close`
- 🟢 `GET /automation/alertdefinitions` — configured alert definitions (🔴 POST/PUT/DELETE to change)
- 🟢 `GET /assetmgmt/logs/{agentId}/eventlog/{application|system|security|directoryservice|dnsserver|internetexplorer}` — event logs
- 🟢 `GET /assetmgmt/logs/{agentguid}/{alarms|monitoractions|configurationchanges|logmonitoring}` — monitoring/alarm history
- 🔴 `POST /system/monitorSetEvent/...` — author/manage monitor sets (write)

**MSP AI mapping:** "any open alarms / recent alerts on this client?" and "show this machine's
security event log" are natural **read** tools (alarms + event logs are all GET) — high value, read-only.

---

*Add more modules here as referenced (Patch Management, Remote Control, Info Center, Service Desk,
Policy Management, etc.). Captured 2026-06-03; re-fetch the source pages if Kaseya updates them.*
