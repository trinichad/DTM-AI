# SOP — OpenAI via ChatGPT plan (Codex OAuth), no API key (D-26)

## What this is

The `openai-codex` model provider gives the chat (and the agent loop behind it) access to OpenAI's
frontier model **on the owner's ChatGPT Plus subscription** — the same "Sign in with ChatGPT" auth the
Codex CLI uses — instead of a pay-per-token `OPENAI_API_KEY`. It coexists with the plain `openai`
(API-key) provider; either appears in the model dropdown only when its credential is configured.

```
ModelRouter.resolve("openai-codex:gpt-5.5")
   └── CodexProvider (execution/core/router.py)
         └── codex_auth.ensure_fresh(cfg)   # valid access token, auto-refreshed
               └── POST https://auth.openai.com/oauth/token   (refresh grant, official
                   Codex CLI client_id app_EMoamEEZ73f0CkXaXp7hrann)
         └── POST https://chatgpt.com/backend-api/codex/responses   (SSE, stream REQUIRED)
```

## Credentials (I-2 / I-3)

| Key | Role |
|---|---|
| `OPENAI_CODEX_REFRESH_TOKEN` | the durable credential (required) — rotated on each refresh |
| `OPENAI_CODEX_ACCESS_TOKEN`  | short-lived JWT cache (~10 days); re-minted from the refresh token |

- Registered as integration `openai_codex` in `core/credentials.py` (group `llm`) → the dashboard
  credential form and fingerprint display work unchanged.
- Stored in `secrets.local` via `SecretStore` so the refresher can WRITE BACK rotated tokens.
  **Do not put these in process env or `.env`** — env shadows the store (config precedence), so a
  refreshed token couldn't be persisted and auth would die when the env copy expires.
- The access token is a JWT; its `exp` claim and the `chatgpt_account_id` auth claim are decoded
  locally (`codex_auth._claims`) — no extra storage, no network call to introspect.
- Originally imported from the retired Hermes container's `/srv/hermes-data/auth.json` (2026-06-10).
  Re-linking never needs a CLI: the dashboard's OpenAI card has **Sign in with ChatGPT** (device flow
  below), the same way Hermes connected.

## Connect from the GUI — device-code flow (verified live 2026-06-10)

The OpenAI integration card offers BOTH auth modes: paste an `OPENAI_API_KEY`, **or** click
*Sign in with ChatGPT* (no key). The sign-in is OpenAI's Codex device-authorization flow — the same
custom (non-RFC-8628) flow the Codex CLI uses:

1. **Start** — `POST https://auth.openai.com/api/accounts/deviceauth/usercode`
   JSON `{client_id}` → `{device_auth_id, user_code, interval (string!), expires_at}`.
   Backend route: `POST /api/integrations/openai_codex/oauth/start`.
2. **Human step** — the GUI shows a link to `https://auth.openai.com/codex/device` plus the one-time
   `user_code`; the owner opens the link in their browser, signs in to OpenAI, and enters the code.
   (Device codes are a phishing target — the GUI shows the code only to the logged-in dashboard user.)
3. **Poll** — `POST https://auth.openai.com/api/accounts/deviceauth/token`
   JSON `{device_auth_id, user_code}`. 403 `deviceauth_authorization_pending` while waiting; on
   approval returns `{authorization_code, code_challenge, code_verifier}` (server-held PKCE).
   Backend route: `POST /api/integrations/openai_codex/oauth/poll` — one upstream poll per call; the
   GUI re-calls every `interval` seconds and gives up after the 15-minute expiry.
4. **Exchange** — `POST https://auth.openai.com/oauth/token` (form-urlencoded)
   `grant_type=authorization_code, code, redirect_uri=https://auth.openai.com/deviceauth/callback,
   client_id, code_verifier` → `{id_token, access_token, refresh_token}` → persisted to the
   SecretStore (audited as `credential_set`, fingerprints only). GPT-5.5 appears in chat immediately.

Cloudflare in front of `auth.openai.com` blocks default the `Python-urllib` UA — every call sends the
Codex CLI user-agent (`codex_auth._UA`). Disconnect = the card's *Disconnect ChatGPT* button (clears
both token keys via the normal credentials endpoint).

## Token lifecycle (`execution/core/codex_auth.py`)

- `ensure_fresh(cfg)` → `(access_token, account_id)`. Returns the cached access token while it has
  > 5 min of life; otherwise POSTs the refresh grant and persists BOTH new tokens through
  `SecretStore.set_many` (atomic, 0600). A module lock serializes concurrent refreshes.
- Fail-closed (Rule #8): no refresh token → `MissingCredential`; refresh HTTP failure → raise, no
  anonymous/partial call is ever attempted.

## Wire format (verified live 2026-06-10)

The Codex backend speaks the **Responses API**, not `/chat/completions`:

- `POST {base}/responses` with headers `Authorization: Bearer <access>`, `chatgpt-account-id`,
  `OpenAI-Beta: responses=experimental`, `originator: codex_cli_rs`, `session_id: <uuid4>`.
- Body: `model`, `instructions` (system text goes here), `input` items, flat `tools`
  (`{type:"function", name, description, parameters, strict:false}`), `store:false`, `stream:true`.
- **`stream:true` is mandatory** — the backend 400s on non-stream. `CodexProvider.chat()` just runs
  `chat_stream()` with a no-op emitter.
- Neutral→wire mapping: user/assistant text → `message` items (`input_text` / `output_text`);
  assistant tool calls → `function_call` items (arguments JSON-string, `call_id`); tool results →
  `function_call_output`. Round-tripping function calls WITHOUT reasoning items is accepted.
- SSE events consumed: `response.output_text.delta` (answer), `response.reasoning_summary_text.delta`
  (emitted on the `thinking` channel, never persisted), `response.output_item.done` (collect
  `function_call` items), `response.completed` / `response.failed`.

## Models

Only **`gpt-5.5`** is accepted for ChatGPT-account Codex auth (the backend 400s every other id —
`gpt-5.1`, `gpt-5.5-codex`, `gpt-4o`, … were probed). The catalog entry is intentionally one model;
re-probe occasionally as OpenAI rotates the lineup.

## Guardrails unchanged

`MSPAI_ALLOW_CLOUD=0` hides/blocks it like every cloud model (I-4); selecting an `ollama:` model keeps
client data local (D-3); all tool calls still flow through `dispatch()` with category enforcement and
audit. The subscription is the owner's personal ChatGPT plan — treat rate limits accordingly (the
backend returns 429s when the plan's Codex quota is exhausted; the provider surfaces the error verbatim).

## Lessons

- 2026-06-10 — backend rejects `stream:false` ("Stream must be set to true"); provider must always
  stream and aggregate for the non-streaming path.
- 2026-06-10 — model ids other than `gpt-5.5` are rejected for ChatGPT-plan auth even when they exist
  for API-key Codex; don't mirror the API-key catalog here.

## Amendment (2026-06-23, D-100) — request reasoning summaries (the panel was always empty)

The provider listens for `response.reasoning_summary_text.delta` (→ `thinking` channel), but the
request body never asked for it, so the Codex backend emitted NONE — gpt-5.5 reasoned silently and
the UI's Reasoning panel (D-61) stayed blank ("I want to see the agent's thought process"). Fix:
`CodexProvider._build_body` now sends `"reasoning": {"summary": "auto"}`. Summaries are display-only
— never persisted, never round-tripped as input (the SOP already notes function calls round-trip
WITHOUT reasoning items, so this is safe). The backend now streams reasoning-summary deltas that the
existing handler + FE render live.
