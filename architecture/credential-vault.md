# SOP — Encrypted Credential Vault + Human Append (A-layer)

> Implements **D-25**. The most security-sensitive store in the system — read before changing.
> Code: `execution/core/credvault.py`, routes `/api/credvault/*` + `/api/clients/<id>/credentials*`
> (`execution/web/api.py`), UI in the client credentials manager + a top-bar lock control.
> Golden rule (A.N.T.): update this SOP before the code.

## Goal
Let the owner store real client credentials (O365 admin, firewall, RMM, …) so the platform can one day
ACT with them — while guaranteeing the **AI can use a credential but never read it**, the secret **never
enters the chat or leaves the box**, and the store is **encrypted at rest**. Multiple labeled creds per
client. Plus an optional **human append**: part of a password that is never stored and is typed by a
person at use-time.

## What the agent can and cannot see
- **CAN see** (read tool `client_credentials`, CATEGORY=read): the LABELS for a client, which FIELD
  NAMES exist (username/password/url/…), and whether an append is required. So it knows what's available
  and what it must request — e.g. "acme has `o365_global_admin` (username, password) — needs end-append".
- **CANNOT see**: any value. There is no tool that returns a secret. Resolution is server-side only.

## Resolution (use-but-not-read) — `CredVault.resolve(tenant, label, appends)`
The future connector tool hands the backend a HANDLE (`cred:acme/o365_global_admin`) + any appends the
human supplied; `resolve()` decrypts in memory, assembles the secret, and returns it ONLY to the
connector's outbound call. It is never placed in a tool result, the transcript, or an LLM message. No
connector ships in this build (write phase) — `resolve()` + the unlock prompt are the ready hook.

## Crypto + key handling
- Cipher: `cryptography` **Fernet** (AES-128-CBC + HMAC-SHA256, authenticated).
- Key: derived from a **master passphrase** via stdlib `hashlib.scrypt` (n=2¹⁴, r=8, p=1, 32 bytes) with a
  per-vault random **salt** (stored plaintext in `.credvault.json` — a salt isn't secret), then
  urlsafe-b64 for Fernet.
- The derived key lives **only in process memory** after `unlock(passphrase)`, with a TTL
  (`DTM_CREDVAULT_TTL_MIN`, default 480) and a manual `lock()`. Never written to disk. After a process
  restart the vault is **locked** — the agent cannot use any credential until an admin unlocks it.
- `.credvault.json` (plaintext, 0600): `{salt, kdf, verifier}`. `verifier` = a Fernet token of a known
  sentinel; a correct passphrase decrypts it, so we validate without storing the passphrase.
- `set_passphrase` (first run) creates the verifier; `change_passphrase` re-encrypts every client file.

## The human append (split secret)
A stored password may embed `{start_append}` and/or `{end_append}`. On `resolve()`:
1. backend sees the placeholder(s) → returns/raises `AppendRequired` (which placeholders, no value);
2. the caller prompts a HUMAN for those pieces (UI prompt; same trust pattern as approvals);
3. the human submits → backend substitutes in memory → uses → discards.
The append is **never stored, never logged, never shown to the agent**. So even a full compromise of the
encrypted file *and* its key yields an incomplete secret. The owner can also "test assemble" a credential
from the UI to prove the prompt works — it reports success (assembled length / fingerprint), never the value.

## Storage shape
`<vault>/clients/<tenant>/credentials.enc` — Fernet blob of:
```jsonc
{ "version": 1, "creds": [
  { "label": "o365_global_admin", "fields": {"username":"…","password":"Password123{end_append}","url":"…"},
    "append": {"start": false, "end": true}, "notes": "…", "updated": "ISO", "updated_by": "user" } ] }
```

## Safety floors (unchanged)
Admin-gated, every mutation + unlock/lock + resolve audited. Fingerprint-only display (I-3). The vault is
a web-admin + connector surface — the agent loop can read labels (one CATEGORY=read tool) but has **no
tool that returns a value and no route to resolve()**. Gitignored. The append is the last-line defense.
