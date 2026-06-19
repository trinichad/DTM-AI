# SOP — Freshdesk ticketing connector (D-83)

A full Freshdesk API v2 integration (MSP AI's own ticketing). Same shape as the other vendor
connectors: a credentialed client (`clients/freshdesk.py`), a CredentialSpec, a factory builder, a
read allowlist (`scopes.py`), and skills grouped in the Capabilities tab.

## Auth & setup
- Creds: `FRESHDESK_DOMAIN` (subdomain `acme`, or `acme.freshdesk.com`, or full URL — normalized to
  `https://acme.freshdesk.com/api/v2`) + `FRESHDESK_API_KEY`.
- Basic auth: API key as username, password ignored (`key:X`). The agent who owns the key bounds
  what writes can do — use a key from an agent with the right role.
- Rate limit is per-ACCOUNT (100/min Free → 700/min Enterprise). Client uses an 80/min sliding
  window + 429 backoff (shared across chat loop + scheduler).

## Bounded writes
Client `WRITE_RULES` / `DESTRUCTIVE_RULES` allowlist (method, path-regex) — same defense-in-depth as
Kaseya/Cylance. dispatch() still gates whether a write runs (CATEGORY=write + Capability Console +
approval). Deletes (ticket/contact/company/group/time-entry) are CATEGORY=destructive → always
per-action approval, never batch-approvable.

## Tools (41, all off by default; integer priority/status surfaced as words via _freshdesk_common)
- **freshdesk_tickets** (12): list, get, conversations, search, create, update, reply (PUBLIC →
  customer, high-risk), add_note (PRIVATE default), forward (external, high-risk), merge, restore,
  delete (destructive).
- **freshdesk_contacts** (12): list/get/search contacts, list/get companies, create/update both,
  make_agent (consumes a seat, high-risk), delete contact/company (destructive).
- **freshdesk_team** (5): list_agents, list_groups, create/update/delete group (delete destructive).
- **freshdesk_time** (4): list/create/update/delete time entries (HH:MM; delete destructive).
- **freshdesk_kb** (5): list categories/folders/articles, create/update article (draft|published).
- **freshdesk_admin** (3): ticket_fields (custom-field names/choices), satisfaction_ratings (CSAT),
  freshdesk_read (generic allow-listed GET).

## Maintenance
On a write 4xx the surfaced error carries Freshdesk's message. Search endpoints wrap in
{results,total} (client unwraps); list endpoints return bare arrays. ticket_fields is the source of
truth for custom-field names before create/update.
