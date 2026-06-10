"""Native delegation — the in-house replacement for Hermes' kanban board (D-19).

A delegation task is assigned to a specialist profile and executed by a worker that runs DTM AI's
OWN agent loop AS that profile (its SOUL + memory + the same guarded tools) — no external runtime,
no `docker exec`, no privileged sudo wrapper. The board is a normal DTM AI store (SQLite dev →
Postgres prod, same pattern as Audit/Approval/Conversation), so it's owned, auditable, and backed up
with everything else.

Two pieces:
  TaskStore   persistence + the board/task shapes the dashboard already renders.
  Dispatcher  claims `ready` tasks and runs each through the agent loop in a background thread.

Status lifecycle (board columns):
  triage → todo → ready → running → review → blocked → scheduled → done   (+ archived, hidden)
A task created WITH an assignee lands in `ready` (dispatchable now); unassigned lands in `triage`.
A worker that finishes moves the task to `review` (a human checks the answer, then archives); a
worker that errors moves it to `blocked` and records the failure.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _PROJECT_ROOT / "dtm_ai.db"

# Board columns, in display order. `archived` is hidden unless explicitly requested. Kept identical
# to the previous board so the dashboard renders unchanged.
BOARD_ORDER = ["triage", "todo", "ready", "running", "review", "blocked", "scheduled", "done"]
_VALID_STATUS = set(BOARD_ORDER) | {"archived"}

_NAME_RE = __import__("re").compile(r"^[a-z0-9_-]+$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    body          TEXT DEFAULT '',
    assignee      TEXT DEFAULT '',
    status        TEXT NOT NULL,
    priority      INTEGER DEFAULT 0,
    created_by    TEXT DEFAULT 'dtm-ai',
    tenant        TEXT DEFAULT '',
    created_at    INTEGER NOT NULL,
    started_at    INTEGER,
    completed_at  INTEGER,
    consecutive_failures INTEGER DEFAULT 0,
    last_failure_error   TEXT,
    result        TEXT,
    model_override TEXT,
    idempotency_key TEXT,
    recurring     INTEGER DEFAULT 0,
    schedule_spec TEXT DEFAULT '',
    next_run_at   INTEGER,
    paused        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE TABLE IF NOT EXISTS task_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    profile    TEXT,
    status     TEXT,          -- running | done | error
    outcome    TEXT,          -- ok | fail
    summary    TEXT,
    error      TEXT,
    started_at INTEGER,
    ended_at   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_task ON task_runs(task_id);
CREATE TABLE IF NOT EXISTS task_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    kind       TEXT,
    payload    TEXT,
    created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id);
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_profile(name: str) -> str:
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"invalid profile: {name!r}")
    return name


class TaskStore:
    """Thread-safe SQLite delegation board. ANSI SQL + bound params → mechanical Postgres swap."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._path = str(db_path or _DEFAULT_DB)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Add columns missing from a pre-existing tasks table (CREATE IF NOT EXISTS won't). Idempotent."""
        have = {r["name"] for r in self._conn.execute("PRAGMA table_info(tasks)")}
        for col, ddl in (("recurring", "INTEGER DEFAULT 0"), ("schedule_spec", "TEXT DEFAULT ''"),
                         ("next_run_at", "INTEGER"), ("paused", "INTEGER DEFAULT 0")):
            if col not in have:
                self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {ddl}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── shaping (mirror the board/task contract the dashboard renders) ──
    @staticmethod
    def _card(r: sqlite3.Row) -> dict:
        return {
            "id": r["id"], "title": r["title"], "assignee": r["assignee"] or "",
            "status": r["status"], "priority": r["priority"] or 0,
            "created_by": r["created_by"], "tenant": r["tenant"] or "",
            "created_ms": r["created_at"], "started_ms": r["started_at"],
            "completed_ms": r["completed_at"],
            "consecutive_failures": r["consecutive_failures"] or 0,
            "last_failure_error": r["last_failure_error"],
            "goal_mode": False, "model_override": r["model_override"],
            "has_result": bool(r["result"]),
            "recurring": bool(r["recurring"]), "schedule_spec": r["schedule_spec"] or "",
            "next_run_ms": r["next_run_at"], "paused": bool(r["paused"]),
        }

    # ── writes ──
    def create(self, title: str, body: str = "", assignee: str = "", created_by: str = "dtm-ai",
               tenant: str = "", idempotency_key: str = "", *, recurring: bool = False,
               schedule_spec: str = "", next_run_at: Optional[int] = None) -> dict:
        title = (title or "").strip()
        if not title:
            raise ValueError("task title required")
        assignee = (assignee or "").strip()
        if assignee:
            _safe_profile(assignee)
        idem = (idempotency_key or "").strip()
        # A recurring task waits in `scheduled` until the scheduler flips it to `ready` when due.
        if recurring:
            status = "scheduled"
        else:
            status = "ready" if assignee else "triage"
        now = _now_ms()
        with self._lock:
            if idem:
                ex = self._conn.execute(
                    "SELECT * FROM tasks WHERE idempotency_key=?", (idem,)).fetchone()
                if ex:
                    return self._card(ex)            # idempotent: same key → same task
            tid = "t_" + uuid.uuid4().hex[:8]
            self._conn.execute(
                "INSERT INTO tasks(id,title,body,assignee,status,created_by,tenant,created_at,"
                "idempotency_key,recurring,schedule_spec,next_run_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, title, body or "", assignee, status, created_by or "dtm-ai",
                 (tenant or "").strip(), now, idem or None,
                 1 if recurring else 0, schedule_spec or "", next_run_at))
            self._conn.execute(
                "INSERT INTO task_events(task_id,kind,payload,created_at) VALUES(?,?,?,?)",
                (tid, "created", json.dumps({"assignee": assignee, "status": status,
                                             "schedule": schedule_spec or None}), now))
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            return self._card(row)

    def assign(self, task_id: str, profile: str) -> dict:
        task_id = (task_id or "").strip()
        if not task_id:
            raise ValueError("task_id required")
        unassign = profile == "none" or not profile
        if not unassign:
            _safe_profile(profile)
        now = _now_ms()
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise ValueError(f"unknown task {task_id}")
            new_assignee = "" if unassign else profile
            # assigning a not-yet-started task makes it dispatchable; unassigning sends it to triage
            status = row["status"]
            if status in ("triage", "todo") and not unassign:
                status = "ready"
            elif unassign and status == "ready":
                status = "triage"
            self._conn.execute("UPDATE tasks SET assignee=?, status=? WHERE id=?",
                               (new_assignee, status, task_id))
            self._conn.execute(
                "INSERT INTO task_events(task_id,kind,payload,created_at) VALUES(?,?,?,?)",
                (task_id, "assigned", json.dumps({"assignee": new_assignee, "status": status}), now))
            self._conn.commit()
            return {"ok": True, "id": task_id, "assignee": new_assignee, "status": status}

    def archive(self, task_id: str) -> dict:
        task_id = (task_id or "").strip()
        if not task_id:
            raise ValueError("task_id required")
        now = _now_ms()
        with self._lock:
            cur = self._conn.execute("UPDATE tasks SET status='archived' WHERE id=?", (task_id,))
            if cur.rowcount != 1:
                raise ValueError(f"unknown task {task_id}")
            self._conn.execute(
                "INSERT INTO task_events(task_id,kind,payload,created_at) VALUES(?,?,?,?)",
                (task_id, "archived", "", now))
            self._conn.commit()
            return {"ok": True, "id": task_id, "archived": True}

    def claim_next_ready(self) -> Optional[dict]:
        """Atomically claim the oldest `ready` task → `running`. Returns the task (with body) or
        None. The conditional UPDATE makes concurrent dispatcher passes race-safe (only one wins)."""
        now = _now_ms()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE status='ready' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            cur = self._conn.execute(
                "UPDATE tasks SET status='running', started_at=? WHERE id=? AND status='ready'",
                (now, row["id"]))
            if cur.rowcount != 1:
                self._conn.commit()
                return None                          # lost the race to another pass
            self._conn.execute(
                "INSERT INTO task_events(task_id,kind,payload,created_at) VALUES(?,?,?,?)",
                (row["id"], "claimed", "", now))
            self._conn.commit()
            return {"id": row["id"], "title": row["title"], "body": row["body"] or "",
                    "assignee": row["assignee"] or "", "tenant": row["tenant"] or "",
                    "recurring": bool(row["recurring"])}

    def start_run(self, task_id: str, profile: Optional[str]) -> int:
        now = _now_ms()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO task_runs(task_id,profile,status,started_at) VALUES(?,?,?,?)",
                (task_id, profile or "", "running", now))
            self._conn.commit()
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, *, ok: bool, summary: str = "", error: str = "") -> None:
        now = _now_ms()
        with self._lock:
            self._conn.execute(
                "UPDATE task_runs SET status=?, outcome=?, summary=?, error=?, ended_at=? WHERE id=?",
                ("done" if ok else "error", "ok" if ok else "fail",
                 summary or None, error or None, now, run_id))
            self._conn.commit()

    def complete_task(self, task_id: str, result: str) -> None:
        """Worker succeeded → move to `review` so a human sees the answer before archiving."""
        now = _now_ms()
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status='review', result=?, completed_at=?, "
                "consecutive_failures=0, last_failure_error=NULL WHERE id=?",
                (result, now, task_id))
            self._conn.execute(
                "INSERT INTO task_events(task_id,kind,payload,created_at) VALUES(?,?,?,?)",
                (task_id, "completed", "", now))
            self._conn.commit()

    def fail_task(self, task_id: str, error: str) -> None:
        now = _now_ms()
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status='blocked', last_failure_error=?, "
                "consecutive_failures=consecutive_failures+1 WHERE id=?", (error, task_id))
            self._conn.execute(
                "INSERT INTO task_events(task_id,kind,payload,created_at) VALUES(?,?,?,?)",
                (task_id, "failed", json.dumps({"error": error[:500]}), now))
            self._conn.commit()

    # ── recurrence (driven by the Scheduler) ──
    def due_recurring(self, now_ms: int) -> list[dict]:
        """Recurring, un-paused tasks waiting in `scheduled` whose next_run_at has passed."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE recurring=1 AND paused=0 AND status='scheduled' "
                "AND next_run_at IS NOT NULL AND next_run_at<=? ORDER BY next_run_at ASC",
                (now_ms,)).fetchall()
            out = []
            for r in rows:
                t = self._card(r)
                t["schedule_spec"] = r["schedule_spec"] or ""
                out.append(t)
            return out

    def enqueue_recurring(self, task_id: str, next_run_at: Optional[int]) -> bool:
        """Fire a due recurring task: `scheduled`→`ready` (dispatchable) and advance next_run_at to the
        NEXT occurrence up front, so a crash mid-run never double-fires. Race-safe via the WHERE guard."""
        now = _now_ms()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status='ready', next_run_at=? WHERE id=? AND status='scheduled'",
                (next_run_at, task_id))
            if cur.rowcount != 1:
                self._conn.commit()
                return False
            self._conn.execute(
                "INSERT INTO task_events(task_id,kind,payload,created_at) VALUES(?,?,?,?)",
                (task_id, "scheduled_fire", json.dumps({"next_run_at": next_run_at}), now))
            self._conn.commit()
            return True

    def complete_recurring(self, task_id: str, result: str) -> None:
        """A recurring run succeeded → return to `scheduled` (NOT `review`); next_run_at already set."""
        now = _now_ms()
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status='scheduled', result=?, completed_at=?, "
                "consecutive_failures=0, last_failure_error=NULL WHERE id=?", (result, now, task_id))
            self._conn.execute(
                "INSERT INTO task_events(task_id,kind,payload,created_at) VALUES(?,?,?,?)",
                (task_id, "recurred", "", now))
            self._conn.commit()

    def set_paused(self, task_id: str, paused: bool) -> dict:
        with self._lock:
            cur = self._conn.execute("UPDATE tasks SET paused=? WHERE id=?",
                                     (1 if paused else 0, task_id))
            if cur.rowcount != 1:
                raise ValueError(f"unknown task {task_id}")
            self._conn.execute(
                "INSERT INTO task_events(task_id,kind,payload,created_at) VALUES(?,?,?,?)",
                (task_id, "paused" if paused else "resumed", "", _now_ms()))
            self._conn.commit()
            return {"ok": True, "id": task_id, "paused": bool(paused)}

    def run_now(self, task_id: str) -> dict:
        """Fire a scheduled task immediately (next tick claims it). Leaves next_run_at intact."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status='ready' WHERE id=? AND status='scheduled'", (task_id,))
            if cur.rowcount != 1:
                raise ValueError(f"task {task_id} not in 'scheduled' state")
            self._conn.execute(
                "INSERT INTO task_events(task_id,kind,payload,created_at) VALUES(?,?,?,?)",
                (task_id, "run_now", "", _now_ms()))
            self._conn.commit()
            return {"ok": True, "id": task_id, "status": "ready"}

    # ── reads (board / task detail) ──
    def list_tasks(self, include_archived: bool = False) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM tasks").fetchall()
            latest: dict[str, str] = {}
            for r in self._conn.execute(
                    "SELECT task_id, summary FROM task_runs WHERE id IN "
                    "(SELECT MAX(id) FROM task_runs GROUP BY task_id)"):
                if r["summary"]:
                    latest[r["task_id"]] = r["summary"]
        out = []
        for r in rows:
            if not include_archived and r["status"] == "archived":
                continue
            t = self._card(r)
            t["latest_summary"] = latest.get(t["id"])
            out.append(t)
        return out

    def board(self) -> dict:
        tasks = self.list_tasks(include_archived=False)
        cols = {s: [] for s in BOARD_ORDER}
        other: list[dict] = []
        for t in tasks:
            (cols[t["status"]] if t["status"] in cols else other).append(t)
        for s in cols:
            cols[s].sort(key=lambda t: t["created_ms"] or 0, reverse=True)
        by_assignee: dict[str, int] = {}
        for t in tasks:
            if t["assignee"]:
                by_assignee[t["assignee"]] = by_assignee.get(t["assignee"], 0) + 1
        return {
            "available": True,
            "columns": [{"status": s, "tasks": cols[s]} for s in BOARD_ORDER],
            "counts": {s: len(cols[s]) for s in BOARD_ORDER},
            "by_assignee": by_assignee,
            "total": len(tasks),
            "other": other,
        }

    def get(self, task_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return None
            t = self._card(row)
            t["body"] = row["body"]
            t["result"] = row["result"]
            t["workspace_kind"] = "native"
            t["comments"] = []                       # native delegation has no comment thread (yet)
            t["runs"] = [
                {"id": r["id"], "profile": r["profile"], "status": r["status"],
                 "outcome": r["outcome"], "summary": r["summary"], "error": r["error"],
                 "started_ms": r["started_at"], "ended_ms": r["ended_at"]}
                for r in self._conn.execute(
                    "SELECT * FROM task_runs WHERE task_id=? ORDER BY started_at", (task_id,))]
            t["events"] = [
                {"kind": e["kind"], "payload": e["payload"], "created_ms": e["created_at"]}
                for e in self._conn.execute(
                    "SELECT kind,payload,created_at FROM task_events WHERE task_id=? "
                    "ORDER BY id DESC LIMIT 40", (task_id,))]
            t["children"] = []
            t["parents"] = []
            return t


class Dispatcher:
    """Runs `ready` tasks through the agent loop AS the assigned profile, in background threads.

    context_factory(tenant, actor) -> ToolContext binds each worker to the task's tenant (tenant
    isolation still absolute). The worker is the SAME guarded agent loop the chat UI uses, so every
    client-touching call still flows through dispatch() (audit, read-only floor, validation)."""

    def __init__(self, store: TaskStore, agent: Any,
                 context_factory: Callable[[str, str], Any],
                 model_resolver: Optional[Callable[[str], Optional[str]]] = None) -> None:
        self.store = store
        self.agent = agent
        self.ctx_factory = context_factory
        # model_resolver(profile) -> DTM model id or None. Lets a specialist run on its own pinned
        # brain (D: per-agent brains). Injected by the runtime so tasks.py stays free of agents.py.
        self.model_resolver = model_resolver

    def dispatch(self, max_n: int = 8) -> dict:
        """Claim up to max_n ready tasks and spawn a worker thread for each. Idempotent."""
        claimed: list[str] = []
        for _ in range(max(1, max_n)):
            task = self.store.claim_next_ready()
            if task is None:
                break
            claimed.append(task["id"])
            threading.Thread(target=self._run_one, args=(task,), daemon=True).start()
        return {"ok": True, "spawned": len(claimed), "claimed": claimed}

    def _run_one(self, task: dict) -> None:
        """Execute one claimed task. Runs synchronously (tests call it directly; dispatch threads it)."""
        profile = task.get("assignee") or None
        run_id = self.store.start_run(task["id"], profile)
        try:
            ctx = self.ctx_factory(task.get("tenant") or "*",
                                   f"delegation:{profile or 'unassigned'}")
            # Run on the specialist's pinned brain, if any. A cloud brain opts THIS run into cloud
            # (still local-first by default, Rule #5); local brains leave allow_cloud untouched.
            model_id = self.model_resolver(profile) if (profile and self.model_resolver) else None
            if model_id and not model_id.startswith(("ollama:", "custom:", "mock")):
                ctx.allow_cloud = True
            msg = task["title"] + (("\n\n" + task["body"]) if task.get("body") else "")
            turn = self.agent.chat(ctx, msg, profile=profile, model_id=model_id)
            summary = (getattr(turn, "answer", "") or "").strip() or "(no answer produced)"
            self.store.finish_run(run_id, ok=True, summary=summary)
            if task.get("recurring"):
                self.store.complete_recurring(task["id"], result=summary)  # back to `scheduled`
            else:
                self.store.complete_task(task["id"], result=summary)       # to `review`
        except Exception as e:                       # contained — a failed task blocks, never crashes
            self.store.finish_run(run_id, ok=False, error=str(e))
            self.store.fail_task(task["id"], str(e))
