"""Sub-group (family) metadata for the Capabilities tab (D-71).

A tool can declare `GROUP = "<id>"` to cluster with its siblings WITHIN its source section. The
matching entry here gives that cluster a title + a markdown setup/how-to panel, so a family like
the Kaseya run-command toolkit is self-explanatory in the list (what it is, the one-time Kaseya
setup it needs, and that everything built on it lives under it). Stdlib-only; pure data.
"""
from __future__ import annotations

GROUP_INFO: dict[str, dict[str, str]] = {
    "kaseya_command": {
        "title": "Command Toolkit — run commands on endpoints",
        "source": "kaseya",
        "icon": "terminal-square",        # shown on the group header AND each tool row (D-71)
        "summary": "The engine that runs commands on a machine through Kaseya, plus every tool "
                   "built on top of it. All of these route through ONE Kaseya procedure you set "
                   "up once.",
        "setup": (
            "**One-time setup in Kaseya (the API can't create procedures — you author this in "
            "the Kaseya console):**\n\n"
            "1. Create an Agent Procedure named **`MSP AI Run Command`** (or any name, then set "
            "the `KASEYA_RUN_COMMAND_PROCEDURE` config to match).\n"
            "2. Give it a **script prompt captioned `command`** — this receives the command "
            "text.\n"
            "3. Add a step that **runs `#command#` in PowerShell** and captures the result into "
            "a variable.\n"
            "4. Add a step that **writes that variable to the asset custom field "
            "`AI_Command_Output`** (or set `KASEYA_COMMAND_OUTPUT_FIELD` to your field name).\n\n"
            "That single procedure powers the whole toolkit. Kaseya's Automation Exchange has "
            "run-command templates to start from."
        ),
        "how_to": (
            "**How it works:** `kaseya_run_command` proposes a command; you approve the exact "
            "command on the approval card; it's scheduled to run on the machine (seconds if the "
            "agent is online). Read the result back with `kaseya_command_output`.\n\n"
            "**Everything here rides the same engine** — `kaseya_install_software`, "
            "`kaseya_network_ping`, `kaseya_restart_service`, etc. each build a SPECIFIC command "
            "(you only supply parameters like the app or service name), so they're bounded and "
            "convenient while sharing the one Kaseya procedure.\n\n"
            "**Safety:** every tool here is approval-gated and off by default. Do NOT turn off "
            "Approval on `kaseya_run_command` — that would allow unattended arbitrary commands. "
            "Use the bell panel's *approve + repeats* for controlled automation bursts."
        ),
    },
}


GROUP_INFO["kaseya_ad"] = {
    "title": "Active Directory & Entra hybrid",
    "source": "kaseya",
    "icon": "users",
    "summary": "AD admin (create/modify users incl. any attribute like proxyAddresses, reset "
               "passwords, unlock, enable/disable, group membership) + Entra delta sync — run as "
               "PowerShell on a DC / sync server through the Command Toolkit engine.",
    "setup": (
        "**Uses the same engine as the Command Toolkit** — needs the one-time `MSP AI Run "
        "Command` Kaseya procedure (see the Command Toolkit setup). No extra setup beyond "
        "that.\n\n"
        "**Run these against your domain controller's machine** (the DC has the ActiveDirectory "
        "PowerShell module). Pass the DC's Kaseya machine name as `server`."
    ),
    "how_to": (
        "Give the AD server (the DC) + the AD details; approve the exact command; read the "
        "result with `kaseya_command_output`.\n\n"
        "**Security note:** for create-user / reset-password, the password is part of the "
        "command — so it appears on the approval card and in Kaseya's command log. That's "
        "inherent to setting an AD password via a command; approve accordingly."
    ),
}


GROUP_INFO["kaseya_fs"] = {
    "title": "File shares & NTFS permissions",
    "source": "kaseya",
    "icon": "folder-lock",
    "summary": "Generic, any-client building blocks for file servers: create a folder (or clone a "
               "template tree) and set NTFS permissions with icacls. String these with "
               "kaseya_ad_create_group + kaseya_ad_add_group_member to provision a client the way "
               "the client property tool does, but for anyone.",
    "setup": (
        "**Uses the same engine as the Command Toolkit** — needs the one-time `MSP AI Run "
        "Command` Kaseya procedure (see the Command Toolkit setup). No extra setup beyond that.\n\n"
        "Run each tool against a machine that can reach the path — a file server, or any box with "
        "the share mapped/UNC-reachable. Pass that machine's Kaseya name as `server`."
    ),
    "how_to": (
        "**Compose them:** `kaseya_fs_provision_folders` creates the folder (optionally cloning a "
        "template tree's sub-folders), then `kaseya_fs_set_permissions` locks it down — "
        "`disable_inheritance` + a `grant` map mirrors a hardened share. Access can be a friendly "
        "word (full/modify/read/readonly/write) or a raw icacls spec like `(OI)(CI)M`.\n\n"
        "**Safety:** every grant REPLACES that principal's entry (predictable, like a client-specific "
        "script); approval-gated and off by default. Approve the exact command on the card."
    ),
}


GROUP_INFO["kaseya_dhcp"] = {
    "title": "DHCP server",
    "source": "kaseya",
    "icon": "network",
    "summary": "Manage a Windows DHCP server: see scopes and how many IPs are free, list "
               "leases/reservations, adjust a scope (name/state/range/lease), and add or remove "
               "reservations and exclusions — run as PowerShell (DhcpServer module) on the DHCP "
               "server through the Command Toolkit engine.",
    "setup": (
        "**Uses the same engine as the Command Toolkit** — needs the one-time `MSP AI Run "
        "Command` Kaseya procedure (see the Command Toolkit setup). No extra setup beyond that.\n\n"
        "**Run these against the Windows DHCP server's machine** — it has the `DhcpServer` "
        "PowerShell module (RSAT / the DHCP role). Pass that machine's Kaseya name as `server`."
    ),
    "how_to": (
        "Scopes are identified by their network address (`scope_id`, e.g. `192.168.1.0`). Start "
        "with `kaseya_dhcp_list_scopes`, check headroom with `kaseya_dhcp_scope_stats` (its "
        "`Free` value is the available IP count), then adjust with `kaseya_dhcp_set_scope` or "
        "manage `…_reservation` / `…_exclusion` (each add has a matching remove). The list tools "
        "are read-only but still approval-gated because everything here runs a command on the "
        "endpoint. Read every result with `kaseya_command_output`."
    ),
}


GROUP_INFO["kaseya_gpo"] = {
    "title": "Group Policy",
    "source": "kaseya",
    "icon": "shield",
    "summary": "Manage Group Policy via the GroupPolicy PowerShell module: list GPOs, create one, "
               "set/remove registry-backed (Administrative Template) settings, see what's actually "
               "applied to a machine (gpresult), force a refresh (gpupdate), link/unlink a GPO to "
               "an OU, and back a GPO up — through the Command Toolkit engine.",
    "setup": (
        "**Uses the same engine as the Command Toolkit** — needs the one-time `MSP AI Run "
        "Command` Kaseya procedure (see the Command Toolkit setup). No extra setup beyond that.\n\n"
        "GPO management (list/link/unlink/backup) runs on a **domain controller** (it has the "
        "`GroupPolicy` module). `gpresult`/`gpupdate` run on the **target machine** itself."
    ),
    "how_to": (
        "Typical flow: `kaseya_gpo_create` → `kaseya_gpo_set_registry` (one Administrative-Template "
        "value per call; `kaseya_gpo_remove_registry` is the matching removal) → `kaseya_gpo_link` "
        "to an OU → `kaseya_gpo_update` to force the refresh. `kaseya_gpo_list` shows all GPOs and "
        "`kaseya_gpo_result` shows what's applied to a machine; `kaseya_gpo_backup` saves a copy.\n\n"
        "**Scope:** `set_registry` covers registry-backed settings (Administrative Templates / "
        "ADMX) — the large majority. UI-only policies (password/account, user-rights, scripts, "
        "software install) aren't registry values and must be set in GPMC. Everything here is "
        "approval-gated (engine-run); read each result with `kaseya_command_output`."
    ),
}

GROUP_INFO["kaseya_dns"] = {
    "title": "DNS server",
    "source": "kaseya",
    "icon": "globe",
    "summary": "Manage Windows DNS via the DnsServer PowerShell module: list zones and records, "
               "add/remove records, resolve a name from a machine, and clear the server cache — "
               "through the Command Toolkit engine.",
    "setup": (
        "**Uses the same engine as the Command Toolkit** — needs the one-time `MSP AI Run "
        "Command` Kaseya procedure (see the Command Toolkit setup). No extra setup beyond that.\n\n"
        "Zone/record/cache tools run on the **DNS server** (it has the `DnsServer` module). "
        "`kaseya_dns_resolve` runs FROM whatever machine you point it at (handy for testing what "
        "a client actually resolves)."
    ),
    "how_to": (
        "Records are identified by zone + name + type + data. `kaseya_dns_add_record` / "
        "`kaseya_dns_remove_record` are a matching pair (A/AAAA/CNAME/MX/TXT/PTR). After a change, "
        "`kaseya_dns_clear_cache` drops stale answers. Read each result with "
        "`kaseya_command_output`."
    ),
}

GROUP_INFO["kaseya_events"] = {
    "title": "Event Viewer",
    "source": "kaseya",
    "icon": "scroll-text",
    "summary": "Read a machine's Windows Event Logs (System/Application/Security/…) with filters "
               "for level, time window, Event ID, and provider — through the Command Toolkit "
               "engine.",
    "setup": (
        "**Uses the same engine as the Command Toolkit** — needs the one-time `MSP AI Run "
        "Command` Kaseya procedure (see the Command Toolkit setup). No extra setup beyond that.\n\n"
        "Runs on the target machine via `Get-WinEvent`. The Security log may need the agent to "
        "run with sufficient rights."
    ),
    "how_to": (
        "`kaseya_event_query` — narrow with `level` (error/warning/…), `since_hours`, `event_id`, "
        "and `provider`, and cap with `count`. Read-only but approval-gated (engine-run). Read "
        "the events with `kaseya_command_output`."
    ),
}


GROUP_INFO["kaseya_registry"] = {
    "title": "Windows Registry",
    "source": "kaseya",
    "icon": "database",
    "summary": "Read and edit the Windows registry on a machine: read a value or list a key, "
               "create/change values, and delete values or whole keys — through the Command "
               "Toolkit engine. (For policy settings that should live in a GPO, prefer the Group "
               "Policy tools instead.)",
    "setup": (
        "**Uses the same engine as the Command Toolkit** — needs the one-time `MSP AI Run "
        "Command` Kaseya procedure (see the Command Toolkit setup). No extra setup beyond that.\n\n"
        "Runs on the target machine. The Kaseya agent runs as **SYSTEM**, so `HKLM` edits are "
        "machine-wide but `HKCU` is SYSTEM's profile, **not** the logged-in user's."
    ),
    "how_to": (
        "Keys are written `HIVE\\path` (HKLM/HKCU/HKCR/HKU/HKCC). `kaseya_registry_get` reads a "
        "value or lists a key; `kaseya_registry_set` creates/changes one (and creates the key "
        "path if needed); `kaseya_registry_delete_value` removes one value; "
        "`kaseya_registry_delete_key` removes a key recursively.\n\n"
        "**Safety:** deleting a whole key is marked **destructive** — it ALWAYS requires a "
        "per-action approval and can never be batch-approved; a bare hive root is refused "
        "outright. Read each result with `kaseya_command_output`."
    ),
}


GROUP_INFO["kaseya_diag"] = {
    "title": "Diagnostics",
    "source": "kaseya",
    "icon": "stethoscope",
    "summary": "Read-only health checks for any machine (server or workstation): top processes, "
               "stopped auto-start services, system/uptime, network config, disk usage, expiring "
               "certs, scheduled tasks, and logged-on sessions — through the Command Toolkit engine.",
    "setup": (
        "**Uses the same engine as the Command Toolkit** — needs the one-time `MSP AI Run "
        "Command` Kaseya procedure (see the Command Toolkit setup). No extra setup beyond that."
    ),
    "how_to": (
        "These never change anything — they gather and report. Run one against a machine, then "
        "read the result with `kaseya_command_output`. They're still approval-gated because "
        "everything here runs a command on the endpoint; use the bell panel's *approve + repeats* "
        "if you're sweeping several machines."
    ),
}

GROUP_INFO["kaseya_net"] = {
    "title": "Network troubleshooting",
    "source": "kaseya",
    "icon": "wifi",
    "summary": "Network diagnostics from a machine: TCP port reachability test, traceroute, and "
               "list/enable/disable/restart of a network adapter — through the Command Toolkit "
               "engine.",
    "setup": (
        "**Uses the same engine as the Command Toolkit** — needs the one-time `MSP AI Run "
        "Command` Kaseya procedure (see the Command Toolkit setup). No extra setup beyond that."
    ),
    "how_to": (
        "`kaseya_net_port_test` and `kaseya_net_traceroute` run FROM the machine you name (so you "
        "test what that PC actually sees). `kaseya_net_adapter` can disable a NIC — be careful "
        "doing that to the adapter a remote machine is reached through. Read results with "
        "`kaseya_command_output`."
    ),
}

GROUP_INFO["kaseya_update"] = {
    "title": "Windows Update",
    "source": "kaseya",
    "icon": "refresh-cw",
    "summary": "Check and install Windows Updates on a machine via the built-in Windows Update API "
               "(no extra module) — through the Command Toolkit engine.",
    "setup": (
        "**Uses the same engine as the Command Toolkit** — needs the one-time `MSP AI Run "
        "Command` Kaseya procedure (see the Command Toolkit setup). No extra setup beyond that."
    ),
    "how_to": (
        "`kaseya_update_check` lists pending + recent updates; `kaseya_update_install` downloads "
        "and installs the pending ones (can run long, and never auto-reboots — it reports "
        "RebootRequired so you can follow with `kaseya_reboot_machine`). Read results with "
        "`kaseya_command_output`."
    ),
}


_FD_SETUP = (
    "**One-time setup:** add a `freshdesk` integration with two values — `FRESHDESK_DOMAIN` (your "
    "Freshdesk subdomain, e.g. `acme` or `acme.freshdesk.com`) and `FRESHDESK_API_KEY` (Profile "
    "Settings → API key in Freshdesk). Auth is the API key as the username; the agent who owns the "
    "key determines what the writes can do, so use a key from an agent with the right role.\n\n"
    "Rate limits are per-account (100/min Free → 700/min Enterprise); the client self-throttles and "
    "backs off on 429."
)

GROUP_INFO["freshdesk_tickets"] = {
    "title": "Tickets",
    "source": "freshdesk",
    "icon": "ticket",
    "summary": "The core helpdesk: list/search/read tickets and their conversations, create and "
               "update them, reply to customers, add internal notes, forward, merge, restore, and "
               "delete.",
    "setup": _FD_SETUP,
    "how_to": (
        "`freshdesk_list_tickets` / `freshdesk_search_tickets` to find work; `freshdesk_get_ticket` "
        "+ `freshdesk_ticket_conversations` to read it; `freshdesk_reply_ticket` (PUBLIC — goes to "
        "the customer) vs `freshdesk_add_note` (PRIVATE by default). Writes are approval-gated and "
        "off by default; replies/forwards are high-risk because they leave the helpdesk. Delete is "
        "destructive (restorable with `freshdesk_restore_ticket`)."
    ),
}

GROUP_INFO["freshdesk_contacts"] = {
    "title": "Contacts & Companies",
    "source": "freshdesk",
    "icon": "users",
    "summary": "End users and the organizations they belong to: list/search/read, create, update, "
               "promote a contact to an agent, and delete.",
    "setup": _FD_SETUP,
    "how_to": (
        "Look up with `freshdesk_list_contacts` / `freshdesk_search_contacts` / "
        "`freshdesk_list_companies`. `freshdesk_make_agent` is high-risk — it consumes a paid agent "
        "seat. Deletes are destructive."
    ),
}

GROUP_INFO["freshdesk_team"] = {
    "title": "Agents & Groups",
    "source": "freshdesk",
    "icon": "user-cog",
    "summary": "Your support staff and routing teams: list agents, and create/update/delete groups.",
    "setup": _FD_SETUP,
    "how_to": "Use `freshdesk_list_agents` to find an agent_id to assign tickets to; manage routing "
              "teams with the group tools. Group delete is destructive.",
}

GROUP_INFO["freshdesk_time"] = {
    "title": "Time tracking",
    "source": "freshdesk",
    "icon": "clock",
    "summary": "Per-ticket time entries for billing reconciliation: list, log, update, delete.",
    "setup": _FD_SETUP,
    "how_to": "`freshdesk_create_time_entry` logs `HH:MM` against a ticket (billable by default). "
              "Delete is destructive.",
}

GROUP_INFO["freshdesk_kb"] = {
    "title": "Knowledge base",
    "source": "freshdesk",
    "icon": "book-open",
    "summary": "The Solutions help center: browse categories → folders → articles, and create or "
               "update articles (draft or published).",
    "setup": _FD_SETUP,
    "how_to": "Drill in with `freshdesk_list_solution_categories` → `…_folders` → `…_articles`; "
              "author with `freshdesk_create_solution_article` (draft by default) and publish via "
              "`freshdesk_update_solution_article`.",
}

GROUP_INFO["freshdesk_admin"] = {
    "title": "Fields, CSAT & raw read",
    "source": "freshdesk",
    "icon": "settings",
    "summary": "Config + reporting reads: ticket fields (incl. custom field choices), satisfaction "
               "(CSAT) ratings, and a generic allow-listed read for anything else.",
    "setup": _FD_SETUP,
    "how_to": "`freshdesk_ticket_fields` tells you the exact custom-field names/values to send when "
              "creating tickets. `freshdesk_read` reaches any allow-listed GET endpoint not covered "
              "by a specific tool.",
}


GROUP_INFO["unifi"] = {
    "title": "UniFi Network",
    "source": "unifi",
    "icon": "wifi",
    "summary": "Manage the local UniFi OS server's Network API: list sites/clients/devices/networks/"
               "SSIDs/vouchers, restart a device, power-cycle a PoE port, block a client, adopt a "
               "device, create guest vouchers, and (advanced) edit firewall/network/SSID config.",
    "setup": (
        "**One-time setup:** add the `unifi` integration with `UNIFI_URL` (the console base, e.g. "
        "`https://unifi.example.com:8443` — the client appends `/proxy/network/integration`) and "
        "`UNIFI_API_KEY`. The key must be generated **on the console** (UniFi Network → Settings → "
        "Control Plane → Integrations / API Keys), NOT a unifi.ui.com cloud key. Self-signed certs "
        "are trusted by default; set `UNIFI_VERIFY_TLS=true` to enforce verification.\n\n"
        "This is the LOCAL per-console API — the cloud Site Manager API (api.ui.com) is separate."
    ),
    "how_to": (
        "Most tools take an optional `site` (name or id) and default to the only/Default site, so "
        "you usually don't pass it. Reads are off by default but safe; writes are approval-gated — "
        "`unifi_restart_device` and `unifi_port_cycle` cause a brief outage, `forget_device` is "
        "destructive. For firewall/network/SSID changes the dedicated tools don't cover, "
        "`unifi_write` (create/update) and `unifi_delete` reach the allow-listed config paths with "
        "the exact body/path shown on the approval card; use `unifi_read` first to see an object's "
        "shape."
    ),
}


GROUP_INFO["kaseya_unifi"] = {
    "title": "UniFi field tools (via Kaseya)",
    "source": "kaseya",
    "icon": "router",
    "summary": "On-site UniFi work that the controller API can't do because the device isn't "
               "adopted/reachable yet: scan a client LAN for Ubiquiti gear, SSH set-inform a device "
               "to the controller, or SSH factory-reset a stuck device — all run from a Windows "
               "machine ON the client's network through the Kaseya command engine.",
    "setup": (
        "**Uses the same engine as the Command Toolkit** — needs the one-time `MSP AI Run Command` "
        "Kaseya procedure (see the Command Toolkit setup). These tools also use **plink (PuTTY)** "
        "for SSH; if it's missing the tool auto-installs PuTTY via Chocolatey, or install it once "
        "on the on-site machine.\n\n"
        "Run against a **Kaseya agent that lives on the SAME LAN/VLAN** as the UniFi device — this "
        "is the whole point: the device isn't reachable from the controller yet, but a local "
        "machine can reach it.\n\n"
        "**Device SSH credentials:** a factory-default UniFi device uses `ubnt`/`ubnt`. An already-"
        "adopted device uses the SSH credentials configured in the controller (Settings → System → "
        "Device SSH Authentication) — pass those as ssh_user/ssh_pass."
    ),
    "how_to": (
        "Typical flow when new gear won't show up: `kaseya_unifi_scan` to find the device's IP → "
        "`kaseya_unifi_set_inform` (device IP + the on-site controller's IP) → adopt it in the "
        "controller → run set-inform once more. `kaseya_unifi_factory_reset` (DESTRUCTIVE) is for a "
        "stuck device that needs to start clean.\n\n"
        "**Security:** the SSH password is part of the command, so it appears on the approval card "
        "and in the Kaseya log (inherent to non-interactive SSH). Factory-reset is destructive — "
        "always per-action approval. Inputs are validated + quoted (injection-safe)."
    ),
}


GROUP_INFO["proofpoint"] = {
    "title": "Proofpoint Essentials",
    "source": "proofpoint",
    "icon": "shield-check",
    "summary": "Manage the Proofpoint Essentials spam filter: read orgs/domains/users, provision or "
               "disable users on the filter, and manage per-user safe (allow) / blocked sender "
               "lists.",
    "setup": (
        "**One-time setup:** add the `proofpoint` integration with `PROOFPOINT_REGION` (your stack: "
        "`us1`–`us5`, `eu1`, etc. — or a full base URL), `PROOFPOINT_USER`, and "
        "`PROOFPOINT_PASSWORD` (an admin or partner account). Auth uses the Essentials "
        "`X-User`/`X-Password` headers. Orgs are addressed by their **primary domain** "
        "(`/orgs/acme.com`); `proofpoint_read` on `/endpoints/{domain}` tells you which stack an "
        "org lives on if you manage multiple."
    ),
    "how_to": (
        "Everyday wins: `proofpoint_allow_sender` / `proofpoint_block_sender` (+ "
        "`proofpoint_remove_sender`) for the 'allow/block this address' helpdesk ask, and "
        "`proofpoint_create_user` / `proofpoint_update_user` (active=false) / "
        "`proofpoint_delete_user` to tie filter provisioning into M365 onboarding/offboarding.\n\n"
        "**Best-effort note:** Essentials' full API reference is login-gated, so the sender-list "
        "field names and a couple of bodies are best guesses — the tools surface the API's error so "
        "we tune them on the first live call. `proofpoint_write` is the bounded escape hatch for "
        "anything the dedicated tools miss."
    ),
}


def info_for(group: str) -> dict[str, str]:
    return GROUP_INFO.get(group or "", {})
