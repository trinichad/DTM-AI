"""Conversation store — server-side, per-user chat history (persistent + multi-chat).

Source of truth for chat transcripts (replaces the old browser-localStorage scheme), so a
MSP tech sees the same conversations from any browser/device and they survive until explicitly
deleted — just like ChatGPT/Claude.

Scoping rules (fail closed):
  - Conversations are PRIVATE to their owner (the logged-in username). Every read/write is
    filtered by owner; a mismatched owner returns None / does nothing — a user can never reach
    another user's chats.
  - Each conversation is bound to one tenant (the client it is about), so the existing
    tenant-isolation guarantees still hold when the agent runs tools for it.

Dev/local: SQLite (stdlib). Same ANSI-SQL + parameter-binding style as audit.py/auth.py, so the
schema ports to Postgres (D-6) mechanically. Lives in the same msp_ai.db.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _PROJECT_ROOT / "msp_ai.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT '*',
    title       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_owner_updated ON conversations(owner, updated_at);
CREATE TABLE IF NOT EXISTS conversation_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,          -- 'user' | 'assistant'
    content         TEXT NOT NULL,          -- user text, or assistant answer
    meta            TEXT,                   -- JSON: tools, citations, provider/model label, error
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON conversation_messages(conversation_id, id);
"""

_TITLE_MAX = 80


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_title(text: str) -> str:
    one = " ".join((text or "").split())
    return (one[:_TITLE_MAX].rstrip() + "…") if len(one) > _TITLE_MAX else (one or "New chat")


class ConversationStore:
    """Thread-safe SQLite store for per-user, multi-conversation chat history."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._conn = sqlite3.connect(str(db_path or _DEFAULT_DB), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ── ownership-scoped row access (fail closed) ─────────────────────────────
    def _row(self, owner: str, conv_id: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT * FROM conversations WHERE id=? AND owner=?", (conv_id, owner))
        row = cur.fetchone()
        return dict(row) if row else None

    def owns(self, owner: str, conv_id: str) -> bool:
        with self._lock:
            return self._row(owner, conv_id) is not None

    def tenant_of(self, owner: str, conv_id: str) -> Optional[str]:
        with self._lock:
            row = self._row(owner, conv_id)
            return row["tenant_id"] if row else None

    # ── CRUD ──────────────────────────────────────────────────────────────────
    def create(self, owner: str, *, tenant_id: str = "*", title: str = "") -> dict:
        conv_id = uuid.uuid4().hex
        ts = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations(id, owner, tenant_id, title, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (conv_id, owner, tenant_id or "*", title or "", ts, ts))
            self._conn.commit()
        return {"id": conv_id, "owner": owner, "tenant_id": tenant_id or "*",
                "title": title or "", "created_at": ts, "updated_at": ts, "message_count": 0}

    def list(self, owner: str) -> list[dict]:
        """All of this owner's conversations, newest activity first, with message counts."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT c.id, c.tenant_id, c.title, c.created_at, c.updated_at, "
                "       (SELECT COUNT(*) FROM conversation_messages m WHERE m.conversation_id=c.id) "
                "         AS message_count "
                "FROM conversations c WHERE c.owner=? ORDER BY c.updated_at DESC", (owner,))
            return [dict(r) for r in cur.fetchall()]

    def get(self, owner: str, conv_id: str) -> Optional[dict]:
        """A conversation plus its messages (meta decoded), or None if not owned/absent."""
        with self._lock:
            row = self._row(owner, conv_id)
            if not row:
                return None
            cur = self._conn.execute(
                "SELECT role, content, meta, created_at FROM conversation_messages "
                "WHERE conversation_id=? ORDER BY id", (conv_id,))
            messages = []
            for m in cur.fetchall():
                meta = None
                if m["meta"]:
                    try:
                        meta = json.loads(m["meta"])
                    except (ValueError, TypeError):
                        meta = None
                messages.append({"role": m["role"], "content": m["content"],
                                 "meta": meta, "created_at": m["created_at"]})
            row["messages"] = messages
            return row

    def history(self, owner: str, conv_id: str, *, limit_msgs: int = 80) -> list[dict]:
        """Prior turns as [{role, content}] for the agent (oldest→newest, capped). Agent re-caps."""
        with self._lock:
            if not self._row(owner, conv_id):
                return []
            cur = self._conn.execute(
                "SELECT role, content FROM conversation_messages WHERE conversation_id=? "
                "ORDER BY id DESC LIMIT ?", (conv_id, max(1, limit_msgs)))
            rows = [{"role": r["role"], "content": r["content"]} for r in cur.fetchall() if r["content"]]
        rows.reverse()
        return rows

    def add_message(self, owner: str, conv_id: str, role: str, content: str,
                    meta: Optional[dict] = None) -> bool:
        """Append a message and bump updated_at. Auto-titles from the first user message."""
        with self._lock:
            row = self._row(owner, conv_id)
            if not row:
                return False
            ts = _now()
            self._conn.execute(
                "INSERT INTO conversation_messages(conversation_id, role, content, meta, created_at) "
                "VALUES(?,?,?,?,?)",
                (conv_id, role, content or "",
                 json.dumps(meta, default=str) if meta else None, ts))
            if role == "user" and not (row["title"] or "").strip():
                self._conn.execute("UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                                   (_derive_title(content), ts, conv_id))
            else:
                self._conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (ts, conv_id))
            self._conn.commit()
            return True

    def resolve_pending(self, owner: str, conv_id: str, approval_id: int, outcome: str) -> bool:
        """Mark the paused-for-approval message as decided (D-47) so its inline buttons don't
        reappear on reload. Finds the message whose meta.pending.id matches and rewrites its meta."""
        with self._lock:
            if not self._row(owner, conv_id):
                return False
            rows = self._conn.execute(
                "SELECT id, meta FROM conversation_messages WHERE conversation_id=? AND role='assistant'",
                (conv_id,)).fetchall()
            for r in rows:
                try:
                    meta = json.loads(r["meta"]) if r["meta"] else {}
                except (TypeError, json.JSONDecodeError):
                    continue
                pend = meta.get("pending")
                if isinstance(pend, dict) and pend.get("id") == approval_id:
                    meta["pending"] = None
                    meta["pending_resolved"] = outcome
                    self._conn.execute("UPDATE conversation_messages SET meta=? WHERE id=?",
                                       (json.dumps(meta, default=str), r["id"]))
                    self._conn.commit()
                    return True
        return False

    def set_tenant(self, owner: str, conv_id: str, tenant_id: str) -> bool:
        """Re-bind a conversation to a client (D-52). Used to LOCK an 'all clients' (*) chat onto
        the specific client the agent ended up working on, so the rest of the thread is scoped to
        it. Owner-checked. Switching to a *different* client is a new conversation (UI), not this."""
        with self._lock:
            if not self._row(owner, conv_id):
                return False
            self._conn.execute("UPDATE conversations SET tenant_id=?, updated_at=? WHERE id=?",
                               ((tenant_id or "*").strip() or "*", _now(), conv_id))
            self._conn.commit()
            return True

    def rename(self, owner: str, conv_id: str, title: str) -> bool:
        title = (title or "").strip()[:_TITLE_MAX] or "New chat"
        with self._lock:
            if not self._row(owner, conv_id):
                return False
            self._conn.execute("UPDATE conversations SET title=? WHERE id=?", (title, conv_id))
            self._conn.commit()
            return True

    def delete(self, owner: str, conv_id: str) -> bool:
        with self._lock:
            if not self._row(owner, conv_id):
                return False
            self._conn.execute("DELETE FROM conversation_messages WHERE conversation_id=?", (conv_id,))
            self._conn.execute("DELETE FROM conversations WHERE id=? AND owner=?", (conv_id, owner))
            self._conn.commit()
            return True

    def compact(self, owner: str, conv_id: str, summary: str, *, keep: int = 2) -> bool:
        """Replace all but the last `keep` messages with a single summary message at the front.

        Mirrors the old client-side Compact, now server-side: shrinks stored context while keeping
        the most recent turns verbatim. Order is preserved by re-inserting in sequence.
        """
        with self._lock:
            if not self._row(owner, conv_id):
                return False
            cur = self._conn.execute(
                "SELECT role, content, meta, created_at FROM conversation_messages "
                "WHERE conversation_id=? ORDER BY id", (conv_id,))
            rows = [dict(r) for r in cur.fetchall()]
            if len(rows) <= keep:
                return False
            tail = rows[-keep:] if keep > 0 else []
            ts = _now()
            self._conn.execute("DELETE FROM conversation_messages WHERE conversation_id=?", (conv_id,))
            self._conn.execute(
                "INSERT INTO conversation_messages(conversation_id, role, content, meta, created_at) "
                "VALUES(?,?,?,?,?)",
                (conv_id, "assistant",
                 "🗜 **Earlier conversation (compacted):**\n" + (summary or ""),
                 json.dumps({"compacted": True, "label": "compacted summary"}), ts))
            for r in tail:
                self._conn.execute(
                    "INSERT INTO conversation_messages(conversation_id, role, content, meta, created_at) "
                    "VALUES(?,?,?,?,?)",
                    (conv_id, r["role"], r["content"], r["meta"], r["created_at"]))
            self._conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (ts, conv_id))
            self._conn.commit()
            return True

    def close(self) -> None:
        with self._lock:
            self._conn.close()
