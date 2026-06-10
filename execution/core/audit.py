"""Append-only audit log (Behavioral Rule: log every call, reads included).

Dev/local: SQLite (stdlib `sqlite3`). Prod: the same schema ports to Postgres via the
DB layer (D-6); this module deliberately uses only ANSI SQL + parameter binding so the
swap is mechanical. Args are stored HASHED (sha256), never raw, to avoid persisting
client data or secrets into the log (matches §2.4 of the constitution).

The table is append-only by convention: this module exposes record()/query() but no
update or delete. Enforce true append-only at the DB grant level in prod.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _PROJECT_ROOT / "dtm_ai.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    actor       TEXT    NOT NULL,
    tenant_id   TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    tool        TEXT,
    category    TEXT,
    args_hash   TEXT,
    result_ok   INTEGER,
    approval_id TEXT,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_ts ON audit_log(tenant_id, ts);
CREATE TABLE IF NOT EXISTS tool_config (
    name        TEXT PRIMARY KEY,
    enabled     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tool_result_cache (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms     INTEGER NOT NULL,
    tenant_id TEXT,
    actor     TEXT,
    tool      TEXT,
    ok        INTEGER,
    data      TEXT
);
CREATE INDEX IF NOT EXISTS idx_trc_actor_ts ON tool_result_cache(actor, ts_ms);
"""

# Ephemeral viewer cache (NOT the append-only audit log): a short, capped preview of a tool's
# result so the chat transcript can show what a tool returned. The full result still flows only
# to the model; this is short-TTL + size-capped so we don't durably accumulate client data.
_RESULT_CAP = 6000
_RESULT_TTL_MS = 30 * 60 * 1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_args(args: Any) -> str:
    try:
        blob = json.dumps(args, sort_keys=True, default=str)
    except Exception:
        blob = str(args)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class AuditStore:
    """Thread-safe SQLite audit store + tool enable/disable config."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._path = str(db_path or _DEFAULT_DB)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            # D-24: also keep a compact, capped JSON of the call args so the owner can see WHAT
            # was requested when clicking into an audit row (args_hash alone is not human-readable).
            have = {r["name"] for r in self._conn.execute("PRAGMA table_info(audit_log)")}
            if "args_json" not in have:
                self._conn.execute("ALTER TABLE audit_log ADD COLUMN args_json TEXT")
            self._conn.commit()

    # ── audit ────────────────────────────────────────────────────────────────
    def record(
        self,
        *,
        actor: str,
        tenant_id: str,
        action: str,
        tool: Optional[str] = None,
        category: Optional[str] = None,
        args: Any = None,
        result_ok: Optional[bool] = None,
        approval_id: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        args_json = None
        if args is not None:
            try:
                args_json = json.dumps(args, default=str)[:2000]   # capped (D-24)
            except Exception:
                args_json = str(args)[:2000]
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log "
                "(ts, actor, tenant_id, action, tool, category, args_hash, result_ok, approval_id, detail, args_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    _now(), actor, tenant_id, action, tool, category,
                    hash_args(args) if args is not None else None,
                    None if result_ok is None else int(result_ok),
                    approval_id, detail, args_json,
                ),
            )
            self._conn.commit()

    def query(self, tenant_id: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            if tenant_id:
                cur = self._conn.execute(
                    "SELECT * FROM audit_log WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
                    (tenant_id, limit),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
                )
            return [dict(r) for r in cur.fetchall()]

    # ── tool-result viewer cache (ephemeral; for the transcript, not the audit) ──
    def cache_result(self, *, tenant_id: str, actor: str, tool: str,
                     ok: bool, data: Any) -> None:
        import time
        try:
            blob = json.dumps(data, default=str)
        except Exception:
            blob = str(data)
        if len(blob) > _RESULT_CAP:
            blob = blob[:_RESULT_CAP] + "…"        # ellipsis marks a truncated preview
        now_ms = int(time.time() * 1000)
        with self._lock:
            self._conn.execute(
                "INSERT INTO tool_result_cache(ts_ms, tenant_id, actor, tool, ok, data) "
                "VALUES (?,?,?,?,?,?)",
                (now_ms, tenant_id, actor, tool, int(bool(ok)), blob))
            self._conn.execute("DELETE FROM tool_result_cache WHERE ts_ms < ?",
                               (now_ms - _RESULT_TTL_MS,))     # prune old previews
            self._conn.commit()

    def recent_results(self, actor: str, since_ms: int, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT ts_ms, tool, ok, data FROM tool_result_cache "
                "WHERE actor=? AND ts_ms>=? ORDER BY ts_ms ASC LIMIT ?",
                (actor, since_ms, limit))
            return [dict(r) for r in cur.fetchall()]

    # ── tool enable/disable (Invariant I-4: config, not code) ─────────────────
    def set_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO tool_config(name, enabled) VALUES(?,?) "
                "ON CONFLICT(name) DO UPDATE SET enabled=excluded.enabled",
                (name, int(enabled)),
            )
            self._conn.commit()

    def is_enabled(self, name: str, default: bool) -> bool:
        with self._lock:
            cur = self._conn.execute("SELECT enabled FROM tool_config WHERE name=?", (name,))
            row = cur.fetchone()
        return bool(row["enabled"]) if row else default

    def enabled_map(self) -> dict[str, bool]:
        with self._lock:
            cur = self._conn.execute("SELECT name, enabled FROM tool_config")
            return {r["name"]: bool(r["enabled"]) for r in cur.fetchall()}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "AuditStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
