# SOP — Huntress & Cylance EDR connectors (D-82)

Both started GET-only. Each client now has a **bounded write surface** mirroring the Kaseya client:
a `WRITE_RULES` (and Cylance `DESTRUCTIVE_RULES`) allowlist of exact `(method, path-regex)` shapes.
`write()` / `write_destructive()` refuse anything else. Whether a write may run AT ALL is still
decided upstream in `dispatch()` (CATEGORY=write ⇒ Capability Console `allow_write` + approval); the
client allowlist is defense-in-depth on the reachable surface.

## Cylance (protectapi.cylance.com, v2)
- Reads (allow-listed prefixes incl. new /globallists/v2, /instaquery/v2): device_detail,
  device_threats, list_policies, get_policy, list_zones, list_users, global_list (safe/quarantine),
  threat_devices, threat_download_url, list_detections (Optics).
- Writes: assign_policy (PUT /devices/v2/{id} — fetches current name first), update_threat
  (PUT …/threats, event Quarantine|Waive), globallist_add/remove (POST/DELETE /globallists/v2,
  list_type GlobalSafe|GlobalQuarantine), create_zone (POST /zones/v2), update_zone (PUT — fetches
  current first), create_instaquery (POST /instaquery/v2).
- Destructive: delete_device (DELETE /devices/v2, body {device_ids:[…]}).
- PREREQ: the Cylance API app (console) must hold the matching permissions (Device/Policy/Global
  List/Zone/Optics) or writes 403. SHA-256 validated 64-hex; ids validated; reasons capped.

## Huntress (api.huntress.io/v1)
- Reads (incl. new /escalations, /summary_reports): list_organizations, get_organization, get_agent,
  get_incident, list_escalations, get_escalation, summary_reports, billing_reports.
- Writes (Huntress's first write APIs): resolve_escalation (POST /escalations/{id}/resolution),
  resolve_incident (POST /incident_reports/{id}/resolution), remediation_respond
  (POST /accounts/{account_id}/incident_reports/{id}/remediations/bulk_approval|bulk_rejection;
  account_id read from /account).
- PREREQ: the API key must carry write scope. Request BODIES are best-effort from public docs (full
  spec is auth-gated) — the tools surface the API error so the owner can iterate. User-management
  writes (create/update/delete users) deferred until the exact endpoint/body is confirmed.

## Maintenance
On a write 4xx, read the surfaced API error first (it carries the vendor message). Cylance PUT
endpoints REPLACE the record — always fetch-then-merge (the tools do). Cylance pagination dedup +
`page` request param caveats are in clients/cylance.py.
