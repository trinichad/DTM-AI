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
"""


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
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log "
                "(ts, actor, tenant_id, action, tool, category, args_hash, result_ok, approval_id, detail) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    _now(), actor, tenant_id, action, tool, category,
                    hash_args(args) if args is not None else None,
                    None if result_ok is None else int(result_ok),
                    approval_id, detail,
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
