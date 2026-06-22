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

## Amendment (2026-06-22) — `chat()` must pause on pending approval too (the D-62 promise was only half-built)

Bug the owner hit: a multi-step write request (assign two licenses + grant full-access + send-as)
stalled — the inline Approve button sat on "Running…", later steps never asked for approval inline,
and approvals showed up only in the bell (one had to be approved there). Send-as was never proposed.

Root cause: the D-62 continuation runs `agent.chat()` (the **non-streaming** loop), and that loop
**never had the `pending_approval` pause** — only `chat_stream` did. So a continuation that reached
the next write didn't stop: dispatch created the approval row, returned the `pending_approval`
envelope, and `chat()` fed that "approval required" back to the model and kept looping — firing the
following writes too. Result: `turn.pending` stayed `None` (no inline card → forced to the bell),
orphan approval rows piled up, and the loop ran extra rounds before narrating, so the synchronous
approve POST (and its "Running…" button) hung far longer than it should. The line above —
"a further write pauses again as a NEW pending approval" — described intended behavior the code
never implemented for `chat()`. `teams_bot.py` and the delegation worker also read `turn.pending`
from `chat()`, so they had the same latent gap.

Fix: `Agent.chat()` now performs the **same `pending_approval` pause as `chat_stream`** — set
`turn.pending`, write the "needs your approval" answer, and return at the FIRST write needing
sign-off. One inline card at a time; no orphan rows; the continuation returns promptly. The
continuation response (and the REST `/api/chat` path) now also carry `tools`/`citations`/`reasoning`
so the post-approval bubble shows the agent's work, not just a bare line. Regression test:
`test_nonstreaming_chat_pauses_on_approval_needed_write` (two queued writes → pauses at the first,
exactly one pending, nothing executed).

## Amendment (2026-06-22b) — stream the continuation + let bell approvals resume the task

Two follow-ups to the pause fix above, both shipped:

**1. The continuation streams live.** Previously the continuation ran *synchronously inside the
approve POST* via `agent.chat()`, so the inline button sat on "Running…" with no feedback until the
whole multi-round turn returned. New SSE endpoint `POST /api/approvals/stream` (admin-gated in
`server.py`, same as the JSON approve): it executes the action, emits a `decided` frame (executed +
deterministic summary), then **streams the continuation turn** through `chat_stream` — the same
`thinking`/`delta`/`tool_call`/`tool_result`/`answer` frames as a normal chat turn, bridged via the
same queue+worker pattern as `stream_chat`, and **stoppable** via `/api/chat/stop` (shares the
`_stops` registry, keyed by conversation). The inline card's **Approve & run** now POSTs here and
paints the continuation into the standard `#ai-streaming` bubble (reusing `paintStream`/`aiHtml`),
so the owner watches tool calls and reasoning arrive in real time, and the next step's approval card
renders from the final `answer` frame's `pending`. The execution/summary/batch-grant logic is
factored into `_run_approval` (shared by the JSON `_approve` and the streaming `stream_approval`).

**2. Bell approvals resume the task.** The approvals table gained a `conversation_id` column
(migrated in place). `dispatch()` stamps it from `ctx._meta["conversation_id"]` (set by every chat
handler) when it records a proposed action. `_approve`/`_reject` resolve the target conversation via
`_resolve_conv`: the one the caller named (inline card) **else the one stored on the row** — so
approving (or rejecting) a chat-originated action *from the bell*, which sends no conversation_id,
now posts the result into the right thread and runs the continuation there (persisted; the owner
sees it on next open). The bell path runs the continuation synchronously (the owner isn't watching a
stream); the inline path streams it. Teams-proposed approvals leave `conversation_id` NULL for the
dashboard (a dashboard admin doesn't own the Teams thread), so a bell decision on one safely does
not try to resume it. Tests: `test_dispatch_records_the_originating_conversation_on_the_approval`,
`test_bell_approval_resumes_via_stored_conversation`, `test_stream_approval_executes_then_streams_continuation`,
`test_stream_approval_rejects_double_decision`.

## Amendment (2026-06-22, D-92) — continuation must ACT, not narrate ("submitted/pending" hallucination)

Owner hit this on "hide GAL for dtmaz1 and dtmaz2": approved dtmaz1's cloud-management enable, then
the continuation produced text claiming it "submitted the required enable cloud management step for
dtmaz2 for approval — pending approval" and stopped. But the DB showed **no approval row for
dtmaz2** — the model never called the tool; it narrated the next step (parroting the canned "I've
prepared X… needs your approval" phrasing) instead of invoking it. No tool call → no approval → no
card → the multi-target task silently stalled after the first target. (Not a rendering or dispatch
bug — the pause/stream/card paths were all working.)

Cause: the continuation's synthetic instruction said "perform any remaining steps, and give the
owner a short status reply", which let the model treat "describe the next step" as a valid action
and emit a fake status. Fix: `Api._continuation_note(row, env)` (one shared builder, replacing the
two divergent inline copies; Teams' copy updated to match) now forces ACTION over narration —
"actually CALL the necessary tool NOW for any remaining step, INCLUDING the same action for other
targets the owner named; NEVER say something is 'submitted'/'pending approval'/'done' unless you
actually called that tool this turn and saw its result." It still forbids re-running the just-run
action with the same args, and only invites a status reply when nothing remains. The JSON
continuation ctx also now carries `conversation_id` (parity with the streamed path) so any approval
it creates is bell-resumable. Test: `test_approve_runs_a_continuation_turn_on_the_conversations_model`
asserts the note carries APPROVED/ALREADY RAN + "CALL the necessary tool".
