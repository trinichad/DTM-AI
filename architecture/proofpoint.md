# SOP — Proofpoint Essentials connector (D-86)

Connector for the Proofpoint Essentials spam-filter API v1 (the SMB/MSP product MSP AI resells; mail
already routes through it — see exo_setup_proofpoint_bypass). Same shape as the other vendor
connectors.

## Auth & setup
- Creds: `PROOFPOINT_REGION` (stack: us1–us5/eu1, or a full base URL) + `PROOFPOINT_USER` +
  `PROOFPOINT_PASSWORD` (admin/partner account). Auth via the Essentials `X-User`/`X-Password`
  headers. Base → `https://<region>.proofpointessentials.com/api/v1`.
- Orgs are addressed by their PRIMARY DOMAIN: `/orgs/acme.com`, users `/orgs/acme.com/users/{email}`.
  `/endpoints/{domain}` tells you which stack an org is on.

## Bounded writes
Client WRITE_RULES (POST/PUT to /orgs/{domain} and /orgs/{domain}/users[/{email}]) +
DESTRUCTIVE_RULES (DELETE user). dispatch() gates every write (CATEGORY + Capability Console +
approval). delete_user = destructive (always per-action approval).

## Tools (12, off by default)
- Reads: get_org, list_domains, list_users, get_user, proofpoint_read (generic allow-listed GET).
- Writes: create_user, update_user (active=false to disable for offboarding), allow_sender,
  block_sender, remove_sender (safe/blocked, GET-then-PUT), proofpoint_write (generic bounded POST/PUT).
- Destructive: delete_user.

## Best-effort / unknowns
The Essentials API reference is login-gated. CONFIRMED-ish: orgs/users CRUD paths + X-User/X-Password
auth. BEST-EFFORT (tune on first live call, tools surface the API error): sender-list attribute names
(`safe_sender_list`/`blocked_sender_list`), create/update user body field names
(primary_email/firstname/surname/is_active), and whether the v1 auth is header-based vs Basic. If a
call 4xx's, read the surfaced error and adjust the field names in _proofpoint_common / the user
skills (or use proofpoint_write to probe the right shape). Quarantine-release + message-trace are
weak/UI-only in Essentials and were not built; the Essentials Threat API (threat/click events) is a
later add.
