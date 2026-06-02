"""ApprovalStore — the write-action approval workflow (Behavioral Rule #1).

When the AI wants to run a write/destructive tool that requires approval, dispatch() does NOT
execute it. Instead it records a PROPOSED ACTION here (tool + exact args + tenant + who asked).
A human reviews it in the dashboard and Approves or Rejects. On approve, the backend executes
the stored action server-side (args-bound — it runs exactly what was proposed, nothing else) and
records the result. One-shot: a decided approval can never be re-run.

This is what lets write capability ramp safely: nothing mutating happens without an explicit,
audited human decision on the precise action.

Dev/local: SQLite (shared db). Prod: same schema ports to Postgres (D-6).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _PROJECT_ROOT / "dtm_ai.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    actor       TEXT NOT NULL,            -- who/what proposed it
    tenant_id   TEXT NOT NULL,
    tool        TEXT NOT NULL,
    category    TEXT NOT NULL,
    args_json   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending|approved|rejected|executed|failed
    decided_by  TEXT,
    decided_ts  TEXT,
    result_ok   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApprovalStore:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._conn = sqlite3.connect(str(db_path or _DEFAULT_DB), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def create(self, *, actor: str, tenant_id: str, tool: str, category: str,
               args: Any) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO approvals(ts, actor, tenant_id, tool, category, args_json, status) "
                "VALUES(?,?,?,?,?,?, 'pending')",
                (_now(), actor, tenant_id, tool, category, json.dumps(args, default=str)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get(self, approval_id: int) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["args"] = json.loads(d["args_json"])
        return d

    def list(self, status: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM approvals WHERE status=? ORDER BY id DESC LIMIT ?",
                    (status, limit)).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM approvals ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["args"] = json.loads(d["args_json"])
            out.append(d)
        return out

    def count_pending(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) c FROM approvals WHERE status='pending'").fetchone()["c"]

    def _decide(self, approval_id: int, status: str, by: str,
                result_ok: Optional[bool] = None) -> bool:
        """Transition a PENDING approval. Returns False if it wasn't pending (one-shot guard)."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE approvals SET status=?, decided_by=?, decided_ts=?, result_ok=? "
                "WHERE id=? AND status='pending'",
                (status, by, _now(), None if result_ok is None else int(result_ok), approval_id),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def reject(self, approval_id: int, by: str) -> bool:
        return self._decide(approval_id, "rejected", by)

    def claim_for_execution(self, approval_id: int, by: str) -> bool:
        """Atomically move pending -> approved so it can't be double-executed."""
        return self._decide(approval_id, "approved", by)

    def mark_result(self, approval_id: int, result_ok: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE approvals SET status=?, result_ok=? WHERE id=?",
                ("executed" if result_ok else "failed", int(result_ok), approval_id))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
