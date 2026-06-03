# SOP — Conversation Persistence (server-side, multi-chat)

> A-layer SOP for the chat-history feature. Golden rule (I-7): update this before the code.

## Goal
A DTM tech gets ChatGPT/Claude-style chat: multiple conversations, a sidebar to switch between
them, history that **persists on the server** (not the browser) until explicitly deleted, visible
from any device/browser.

## Where it lives
- **Store:** `execution/core/conversations.py` — `ConversationStore` (SQLite, same `dtm_ai.db`,
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
- Pre-existing browser `localStorage` chats (`dtm_chat`) are NOT migrated — they're orphaned by
  design; the server store starts empty. (If migration is ever wanted, import on first load.)
- Build history BEFORE persisting the incoming user message, then persist user + assistant after the
  turn — otherwise the current message would double-count in the agent's history window.
- The frontend keeps optimistic rendering (push to `MSGS`, show typing dots) and only calls
  `refreshConvs()` after a successful turn to pick up the new id / auto-title / reordering — avoids
  full-transcript reload flicker on every send.
