# SOP — Conversation Persistence (server-side, multi-chat)

> A-layer SOP for the chat-history feature. Golden rule (I-7): update this before the code.

## Goal
A MSP tech gets ChatGPT/Claude-style chat: multiple conversations, a sidebar to switch between
them, history that **persists on the server** (not the browser) until explicitly deleted, visible
from any device/browser.

## Where it lives
- **Store:** `execution/core/conversations.py` — `ConversationStore` (SQLite, same `msp_ai.db`,
  same ANSI-SQL/parameter-binding style as `audit.py`/`auth.py` so it ports to Postgres, D-6).
- **Wiring:** `runtime.build_agent()` constructs it and exposes `agent.conversations`.
- **API:** `execution/web/api.py` — `/api/conversations` (GET list, POST create),
  `/api/conversations/{id}` (GET full, DELETE), `/api/conversations/{id}/rename`,
  `/api/conversations/{id}/compact`. `/api/chat` now persists each turn.
- **UI:** `dashboard/index.html` `chat()` — two-column layout: a conversation rail
  (new chat / list / rename / delete) + the chat column. State: `CONV`, `MSGS`, `CONVS`.

## Data model
- `conversations(id, owner, tenant_id, title, created_at, updated_at)`
- `conversation_messages(id, conversation_id, role, content, meta JSON, created_at)`
  - assistant `meta` carries `tools`, `citations`, and a `label` (provider/model · rounds);
    a compacted summary message carries `{"compacted": true}`.

## Rules (enforced in code, not prose)
1. **Per-user privacy.** Every store method takes `owner` (the logged-in username) and filters by
   it; a mismatched/absent owner returns `None`/`False` — a user can never read, append to, rename,
   or delete another user's conversation. (`tests/test_conversations.py::test_ownership_isolation`,
   `tests/test_api.py::test_conversations_are_per_user`.)
2. **Server is the source of truth.** The browser sends only `conversation_id` + `message`; the
   server loads prior turns from the DB as history. The old client-sent `history` is no longer the
   authority (the `/api/chat` body field is ignored when a `conversation_id` is supplied). This is
   what makes history cross-device and durable.
3. **A conversation owns its tenant.** Tenant isolation (Behavioral Rule #4) is preserved: the agent
   runs for `conversations.tenant_of(...)`, not whatever the client claims mid-conversation.
4. **Auto-title** from the first user message (≤80 chars); later messages never overwrite it.
5. **Compaction is server-side now.** `/api/conversations/{id}/compact` summarises older turns
   (via `agent.summarize`) and `ConversationStore.compact` replaces them with one summary message,
   keeping the last `keep=2` verbatim, order preserved.

## Notes / lessons
- Pre-existing browser `localStorage` chats (`mspai_chat`) are NOT migrated — they're orphaned by
  design; the server store starts empty. (If migration is ever wanted, import on first load.)
- Build history BEFORE persisting the incoming user message, then persist user + assistant after the
  turn — otherwise the current message would double-count in the agent's history window.
- The frontend keeps optimistic rendering (push to `MSGS`, show typing dots) and only calls
  `refreshConvs()` after a successful turn to pick up the new id / auto-title / reordering — avoids
  full-transcript reload flicker on every send.

---

## Amendment (2026-06-11, D-39) — message timestamps, displayed in Eastern time

- Every chat bubble shows its message's time next to the name label ("You · 10:56 AM"). The
  store already had `created_at` (UTC ISO) per message — `normMsg()` now carries it through as
  `ts`, `bubble()` renders it, and live sends stamp optimistically client-side (the server's
  persisted `created_at` takes over on the next transcript load).
- **All app times display in `America/New_York`** (`APP_TZ` constant in the dashboard,
  `fmtTS()`/`fmtTSFull()` helpers): MSP AI runs on Eastern time regardless of the viewer's browser
  timezone. Same-day messages show time only; older ones show the date (+year when different).
- STORAGE stays UTC ISO (audit, conversations, snapshots) — timezone is a display concern only;
  the host box is set to America/New_York as well. The audit detail panel now renders "When" via
  the same helpers (labelled ET) instead of the raw UTC string.

---

## Amendment (2026-06-11, D-45) — stop / interrupt an in-flight turn
The composer's Send button swaps to a red **Stop** while a turn streams. Stop calls
`POST /api/chat/stop {conversation_id}` (owner-scoped — a user can only stop their own
conversation), which sets a per-conversation `threading.Event` registered by `stream_chat`.
`agent.chat_stream(..., should_stop=ev.is_set)` checks it at two safe points: between streamed
tokens (raising `_Interrupted` from the token callback so a long generation halts promptly) and
**before every tool dispatch** — so a queued WRITE never fires after the user hits stop. The turn
returns with `stopped=True` and the partial answer (+ a "_(stopped)_" marker), which is persisted
like any turn; the UI then lets the user type a new message. Sending while a turn is still
streaming is blocked (stop first). Background: `_stops` dict + lock on the `Api` instance; the
entry is removed in `stream_chat`'s `finally`.

---

## Amendment (2026-06-11, D-47) — approval pause/resume inside chat
A client-system write that needs sign-off no longer ends the turn as a failure. `chat_stream`
detects the `pending_approval` dispatch envelope, stops the loop, and returns `turn.pending`
({id, tool, tenant, args}) with a clear "needs your approval" answer (persisted in the assistant
message's `meta.pending`; surfaced in the answer frame + an `approval_required` event). The chat
bubble renders an inline Approve/Reject card (admin-only). Approve → `POST /api/approvals/<id>/
approve {conversation_id}`: the backend executes the args-bound action, posts a deterministic
result summary back as a new assistant message, and `conversations.resolve_pending()` rewrites the
paused message's meta so its buttons don't reappear on reload. Own-vault writes (source=msp_ai)
never reach this — they auto-run (gate floor, D-47). The per-tool "Approval" toggle (Capability
Console) decides whether a client write waits (ON) or auto-runs (OFF).

---

## Amendment (2026-06-11, D-52) — focus_client locks an all-clients chat
From an "All clients" (*) session, the `focus_client` tool locks the conversation onto the single
client the agent is working on. The agent loop narrows `ctx.tenant_id` for the rest of the turn
(per-client tools then run scoped), sets `turn.focus_client`, and emits a `client_locked` event;
`stream_chat` calls `conversations.set_tenant()` to persist the binding and includes `focus_client`
in the answer frame. The UI updates the client picker and rebinds the conversation locally — so a
later switch to a *different* client starts a new chat (D-51). Switching clients is never an
in-place rebind; only the *-→specific narrowing is.

---

## Amendment (2026-06-12, D-59) — batch approval ("approve once, auto-approve the repeats")

Owner chose the generic mechanism for bulk runs (e.g. "enforce MFA for these 40 users") over
per-tool bulk params. Design — a BOUNDED GRANT, not a blanket:

- Both approval surfaces (inline chat card + bell panel) gain **"Approve + repeats"** next to
  the normal Approve. It approves + executes that action as usual, THEN arms a grant.
- A grant is keyed **(tenant, tool)** and auto-approves subsequent calls of THAT tool for THAT
  client only, while it lasts: **count-capped** (default 25, hard max 200) and **TTL'd 15 min**.
  Args may differ per call — that's the point (different users, same operation).
- **Floors unchanged:** `destructive` tools can NEVER be batch-granted (refused at grant time
  AND at consume time — Rule #1 floor); `allow_write` + enable/kill-switch checks still apply
  per call; tenant binding still applies; every auto-approved run is AUDITED with
  `detail="auto-approved by batch grant (approval#N, K of M left)"` and the result envelope
  carries `auto_approved` so the chat shows it.
- The grant only arms when the approved action **actually succeeded** — a failing first run
  never unlocks repeats.
- **Visibility + kill:** active grants are listed in the bell panel with a Revoke button;
  `POST /api/approvals/batch/revoke` clears them; grants live in-process (a restart clears
  them, which is fail-safe).
- Implementation: `ConfigurableApprovalGate.grant_batch/_take_batch/list_batches/
  revoke_batches` (gates.py); `dispatch()` surfaces the consumption note; `_approve` accepts
  `{batch: true, batch_count}`.

---

## Amendment (2026-06-12, D-61) — visible reasoning / chain-of-thought panel

Owner wants the model's reasoning visible like ChatGPT shows it, for every model. Design:

- **One uniform channel.** Providers map their NATIVE reasoning stream onto the existing
  `emit(text, "thinking")` channel: Ollama → `message.thinking` (qwen-style reasoning models);
  Codex/gpt-5.5 → `response.reasoning_summary_text.delta` (OpenAI exposes reasoning SUMMARIES,
  never raw CoT — same as ChatGPT's UI); Claude → `thinking_delta` handler is wired for when
  extended thinking is enabled. Any future provider just emits on the same channel.
- **Persisted, not ephemeral (the change).** The agent accumulates the thinking stream into
  `AgentTurn.reasoning` (capped 24k chars); it is stored in the conversation message meta and
  returned in the answer frame — so reasoning survives reloads and appears in history.
  It is NEVER fed back into the model's context (clean_history ignores meta) and never part of
  `answer`/citations.
- **UI:** while streaming, a collapsible "Thinking…" panel shows the full live reasoning (open
  until the first answer token, then auto-collapses unless the user pinned it open; click
  toggles). Finished bubbles render a closed `Reasoning` disclosure above the answer when the
  turn has any. Models that don't emit reasoning simply show nothing — no fake panel.

---

## Amendment (2026-06-12, D-62) — the agent CONTINUES after an inline approval

Gap: the paused turn said "I'll continue as soon as you decide", but approval only executed the
action and posted a deterministic summary — the agent never resumed (owner hit this: GAL-hide ran,
then silence). Now `_approve` (the inline-chat path, i.e. when a conversation_id is present) runs a
CONTINUATION turn after executing the action:

- The agent is re-invoked on the same conversation history (which now ends with the executed
  result), with a synthetic, NOT-persisted instruction: the owner approved, here is the result
  envelope — verify/finish the task and reply. Its answer is persisted + returned in the approve
  response (`continuation`), and the chat UI appends it as a normal assistant bubble.
- **Model continuity:** the continuation runs on the SAME model as the conversation's last
  assistant turn (parsed from the stored `provider/model` label) — a gpt-5.5 chat continues on
  gpt-5.5, not the local default.
- **Guardrails unchanged:** the continuation goes through the normal agent loop → dispatch; a
  further write pauses again as a NEW pending approval (no recursion — approving never
  auto-approves); a live batch grant (D-59) applies as usual. Failures in the continuation are
  swallowed (logged) — the deterministic summary has already told the owner what happened, so a
  broken follow-up can never mask an executed action.
- Bell-panel approvals (no conversation) and rejections behave as before (no continuation).
