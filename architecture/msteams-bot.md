# SOP — Microsoft Teams bot (chat with MSP AI in Teams — D-29)

> The the MSP team can DM the bot (or @mention it in a group/channel) and get the same guarded,
> audited agent loop as the dashboard chat. Ported from how Hermes did Teams (Bot Framework
> webhook + default-deny user allowlist), reimplemented natively — no SDK, ~no new deps
> (PyJWT + cryptography were already in requirements).

## Architecture

```
Teams client ──> Bot Framework ──HTTPS POST──> nginx ──> /api/teams/messages   (server.py, no cookie)
                                                   │ 1. kill switch (MSPAI_TEAMS, I-4) + configured check
                                                   │ 2. Bot Framework JWT verified (PyJWKClient,
                                                   │    iss=https://api.botframework.com, aud=CLIENT_ID)
                                                   │ 3. activity-id dedup (redelivery)
                                                   │ 4. ALLOWLIST: AAD object id ∈ TEAMS_ALLOWED_USERS
                                                   │    (default DENY; TEAMS_ALLOW_ALL_USERS=true is the
                                                   │    explicit opt-out — mirrors Hermes)
                                                   │ 5. group/channel: only when @mentioned (DMs: always)
                                                   ▼
                                  202 returned immediately; background thread runs
                                  Agent.chat() — profile TEAMS_PROFILE, tenant TEAMS_BIND_TENANT,
                                  allow_cloud only if TEAMS_ALLOW_CLOUD=1 (Rule #5)
                                                   ▼
                                  reply POSTed to {serviceUrl}/v3/conversations/{id}/activities
                                  (service host allowlisted: smba.trafficmanager.net /
                                   smba.infra.gov.teams.microsoft.us — blocks token exfil via a
                                   tampered serviceUrl, ported from Hermes)
```

Modules: `execution/clients/msteams.py` (token cache, JWT verify, send/typing, probe) and
`execution/core/teams_bot.py` (the bridge: checks 1–5, conversation mapping, reply).

## Configuration (Integrations → Microsoft Teams)

| key | meaning |
|---|---|
| `TEAMS_CLIENT_ID` / `TEAMS_TENANT_ID` | **required** — Azure bot app registration |
| `TEAMS_CLIENT_SECRET` | the app secret — optional when an app **certificate** exists (below) |
| `TEAMS_ALLOWED_USERS` | CSV allowlist entries `aad-object-id|Display Name` (name optional). Managed in the card UI. |
| `TEAMS_ALLOW_ALL_USERS` | `true` skips the allowlist — explicit, discouraged |
| `TEAMS_BIND_TENANT` | managed client the Teams sessions are bound to (default `*` = all-clients read view) |
| `TEAMS_PROFILE` | agent profile that answers (default `default` = AtlasOps) |
| `TEAMS_ALLOW_CLOUD` | `1` lets Teams turns use cloud models (default local-first, Rule #5) |
| `TEAMS_HOME_CONVERSATION` | conversation id for proactive alerts (`teams_notify` tool) |
| `TEAMS_SERVICE_URL` | override for gov/regional clouds; must pass the host allowlist |
| `MSPAI_TEAMS` | `0` kills the webhook instantly (I-4) |

## Certificate auth (instead of a client secret)

`core/teams_cert.py` generates a self-signed RSA-2048 cert **on the box** (one PEM file with
key + cert at `teams_bot_cert.pem`, mode 0600, gitignored via `*.pem`; path override
`MSPAI_TEAMS_CERT_PATH`). The owner downloads the public `.cer` from the card and uploads it in
Entra → App registrations → the app → **Certificates & secrets → Upload certificate**.

Token requests then use the client-credentials **JWT client assertion** flow
(`client_assertion_type=jwt-bearer`, RS256, `x5t` thumbprint header) — built in
`TeamsClient._token_request_fields()`. Selection rule: **certificate wins when it exists**,
else the secret; neither → the client refuses to construct (fail closed). The private key is
never returned by any API — only the public cert + thumbprint reach the browser.

Endpoints (admin, audited): `GET/POST/DELETE /api/integrations/msteams/cert`.
Regenerating invalidates the old cert — the new `.cer` must be re-uploaded in Entra.

## Bot registration (same flow Hermes documented)

1. `npm i -g @microsoft/teams.cli@preview && teams login`
2. `teams app create --name "MSP AI" --endpoint "https://<your-domain>/api/teams/messages"`
   → CLIENT_ID / CLIENT_SECRET / TENANT_ID → paste into the card.
3. `teams app install` (or the printed install link). Find AAD object ids for the allowlist with
   `teams status --verbose`, or in Entra → Users.
4. The endpoint is the MAIN dashboard server behind nginx/HTTPS — no extra port. Teams requires a
   valid public TLS cert.

## Conversation + identity mapping

- Each Teams conversation maps to a persistent MSP AI conversation owned by synthetic user
  `teams:<aad-object-id>` (per-user history isolation; matched by conversation title = Teams
  conversation id).
- Audit actor: `teams:<display name> (<aad id>)` — every tool call lands in the audit log as usual.
- Sessions are tenant-bound to `TEAMS_BIND_TENANT` (Rule #4 unchanged — the binding is fixed
  server-side; a Teams user cannot pick a tenant).

## Safety floors (cannot be configured away)

- No valid Bot Framework JWT → 401, body never processed.
- Empty allowlist + no explicit allow-all → **deny everyone** (and say so once per conversation).
- The webhook never executes tools directly — replies come from the same `dispatch()`-guarded
  agent loop; write/destructive still go through the Capability Console + Approvals.
- `teams_notify` (alerts INTO Teams) is `CATEGORY="alert"`, disabled by default.
