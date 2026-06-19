# SOP — UniFi Network connector (D-84)

First-class connector for the LOCAL UniFi Network Integration API on the self-hosted UniFi OS server
(`<console>/proxy/network/integration`). Replaces using the read-only `ubiquiti` custom integration
for write actions. See [[ubiquiti-unifi-local-network-api]] memory + kb/ubiquiti/network-api-reference.md.

## Auth & setup
- Creds: `UNIFI_URL` (console base, e.g. `https://unifi.example.com:8443` — client appends
  `/proxy/network/integration`, idempotent) + `UNIFI_API_KEY` (generated ON THE CONSOLE: UniFi
  Network → Settings → Control Plane → Integrations / API Keys — NOT a unifi.ui.com cloud key).
  Optional `UNIFI_VERIFY_TLS` (default off — LAN self-signed cert; the client passes verify_tls to
  the transport, unlike the cloud clients).
- Header `X-API-Key`. Responses wrap rows in `{offset,limit,count,totalCount,data:[…]}` (client
  unwraps + offset-paginates).

## Site resolution
Most endpoints are under `/v1/sites/{siteId}/…`. `_unifi_common.resolve_site()` turns an optional
site name/id into a siteId, defaulting to the only site, else one named "Default", else the first —
so techs rarely pass `site`.

## Tools (19, all off by default)
- Reads (10): list_sites, list_clients, list_devices, device_detail, device_stats, list_networks,
  list_wifi (SSIDs), list_vouchers, pending_devices, unifi_read (generic allow-listed GET).
- Writes (6): restart_device (RESTART), port_cycle (PoE POWER_CYCLE), client_action
  (block/unblock/reconnect/authorize), adopt_device, create_voucher, unifi_write (generic bounded
  POST/PUT/PATCH for networks/SSID/firewall/DNS/ACL config).
- Destructive (3): forget_device, delete_voucher, unifi_delete (generic bounded DELETE).

## Bounded writes
Client `WRITE_RULES` / `DESTRUCTIVE_RULES` allow-list (method, path-regex). The generic unifi_write/
unifi_delete reach only allow-listed config paths; dispatch() still gates every write (CATEGORY +
Capability Console + approval). Deletes are CATEGORY=destructive (always per-action approval).

## Maintenance / unknowns
Action enum names (client BLOCK/UNBLOCK/RECONNECT/AUTHORIZE_GUEST, device RESTART, port POWER_CYCLE)
and adopt/voucher bodies are best-effort from the API reference; tools surface the API error for
iteration. Firewall/network/SSID create/update bodies are user-supplied via unifi_write (use
unifi_read to see an object's shape first).
