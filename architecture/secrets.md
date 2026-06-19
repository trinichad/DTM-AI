# SOP — Secret Management (A-layer)

> Implements Invariant I-3. Code: `core/secrets_store.py`, `core/config.py`, `core/credentials.py`,
> web `POST /api/integrations/<name>/credentials`.

## Where credentials live (two sources, clear precedence)
`config.Config.get()` resolves in this order:
1. **process env** (container / systemd `EnvironmentFile`) — wins, for prod overrides
2. **`secrets.local`** — app-managed, written by the dashboard credential form (the easy path)
3. **`.env`** — hand-edited file (the manual path)
4. default

So you can add a key by editing `.env` OR via the dashboard (Integrations → Manage keys); the UI store
overrides a manual `.env`, and real process env overrides everything.

## How the UI credential form is safe
- **0600, app-owned** (`secrets.local`, gitignored). Written atomically (temp file, `os.open` 0600,
  `os.replace`). Boot/load refuses if the file is group/world-readable.
- **Key allowlist** — `credentials.set_integration` only accepts keys from that integration's
  `CredentialSpec` (required + optional). You cannot inject arbitrary config (PATH, admin password, …).
- **Write-only from the browser** — the API returns ONLY `sha256[:7]` fingerprints, never the raw value
  (verified: raw value appears in 0 API responses). Empty input keeps the current value.
- **Admin session + audited** — `credential_set` is logged with key names only, never values.
- **Immediate effect** — saving invalidates the cached vendor client (`ClientFactory.invalidate`).

## At-rest protection & hardening path
v1 protection = OS file permissions (0600) + dedicated service user + systemd hardening
(ProtectHome, etc.). The secret bytes are NOT encrypted at rest. Documented upgrade path
(unblocks when needed): back `SecretStore` with the OS keyring (`keyring`) or SOPS/age, or a
cloud KMS — the `SecretStore` interface (`get`/`set_many`/`reload`) is the single seam to swap.

## Edge cases / lessons
- Empty-string value = CLEAR that key (lets you remove a stale credential from the UI).
- `secrets.local` and `.env` and `.session_secret` are all gitignored; a commit-time check guards
  against staging them.
- Adding a NEW integration = add a `CredentialSpec` in `credentials.py` (+ a client + connector);
  its keys then become settable in the UI automatically.
