"""Read (and, via a fenced wrapper, drive) Hermes' kanban delegation board.

Hermes does real cross-profile delegation through a **durable SQLite board shared across all
profiles** (`hermes kanban`): a task is assigned to a named profile and executed by a worker that
the gateway's dispatcher spawns in an isolated workspace — running with that specialist's own SOUL,
memory, and brain. The board DB lives on the shared volume (`<HERMES_HOME>/kanban.db` =
`/srv/hermes-data/kanban.db`), `dtm-ai`-owned, so DTM AI **reads it directly, read-only** — the same
pattern as reading profiles on disk. No `docker exec` needed for reads.

Writes (create/assign a task = delegate) cannot go straight to the DB without bypassing Hermes'
atomic-claim + event-emission invariants, and the web service can't `docker exec`. So delegation is
routed through a locked-down privileged wrapper (`deploy/hermes/dtm-ai-kanban.sh`, whitelisted
actions only) invoked via a tightly-scoped sudoers entry. See `create_task()` / `assign_task()`.
"""
from __future__ import annotations

import json
import os
import shlex
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional

from .config import Config, get_config
from .agents import _data_dir, _safe  # agents dir root + profile-name validation

# Columns we surface, in board order. `archived` is hidden unless explicitly requested.
BOARD_ORDER = ["triage", "todo", "ready", "running", "review", "blocked", "scheduled", "done"]
_VALID_STATUS = set(BOARD_ORDER) | {"archived"}

# The privileged delegation wrapper (installed on the server, root-owned). Overridable for tests.
_WRAPPER = os.environ.get("DTM_HERMES_KANBAN_WRAPPER", "/opt/dtm-ai/deploy/hermes/dtm-ai-kanban.sh")


def _db_path(cfg: Config) -> Path:
    """Resolve the active board's kanban.db.

    Default board = ``<root>/kanban.db``. A non-default selected board (named in the one-line
    ``<root>/kanban/current`` file) lives at ``<root>/kanban/boards/<slug>/kanban.db``. Mirrors
    Hermes' own ``current_board_path()`` so DTM AI reads the same board the dispatcher works.
    """
    root = _data_dir(cfg)
    current = root / "kanban" / "current"
    try:
        slug = current.read_text(encoding="utf-8").strip()
    except OSError:
        slug = ""
    if slug and slug != "default":
        cand = root / "kanban" / "boards" / slug / "kanban.db"
        if cand.is_file():
            return cand
    return root / "kanban.db"


def available(cfg: Optional[Config] = None) -> bool:
    return _db_path(cfg or get_config()).is_file()


def _connect(cfg: Config) -> Optional[sqlite3.Connection]:
    """Open the board READ-ONLY (never mutate another process's live DB from here)."""
    p = _db_path(cfg)
    if not p.is_file():
        return None
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 4000")
    return conn


def _ms(v) -> Optional[int]:
    """Normalize an epoch int to milliseconds (Hermes stores seconds) for the FE."""
    if v is None:
        return None
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return v if v > 1_000_000_000_000 else v * 1000


def _task_row(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "title": r["title"],
        "assignee": r["assignee"],
        "status": r["status"],
        "priority": r["priority"],
        "created_by": r["created_by"],
        "tenant": r["tenant"],
        "created_ms": _ms(r["created_at"]),
        "started_ms": _ms(r["started_at"]),
        "completed_ms": _ms(r["completed_at"]),
        "consecutive_failures": r["consecutive_failures"],
        "last_failure_error": r["last_failure_error"],
        "goal_mode": bool(r["goal_mode"]) if r["goal_mode"] is not None else False,
        "model_override": r["model_override"],
        "has_result": bool(r["result"]),
    }


def list_tasks(cfg: Optional[Config] = None, include_archived: bool = False) -> list[dict]:
    cfg = cfg or get_config()
    conn = _connect(cfg)
    if conn is None:
        return []
    try:
        rows = conn.execute("SELECT * FROM tasks").fetchall()
        # latest run summary per task — workers often answer in the run summary (result stays empty),
        # so the board needs it to show an outcome on a card without opening the task.
        latest: dict[str, str] = {}
        for r in conn.execute(
                "SELECT task_id, summary FROM task_runs WHERE id IN "
                "(SELECT MAX(id) FROM task_runs GROUP BY task_id)"):
            if r["summary"]:
                latest[r["task_id"]] = r["summary"]
    finally:
        conn.close()
    out = []
    for r in rows:
        t = _task_row(r)
        t["latest_summary"] = latest.get(t["id"])
        out.append(t)
    if not include_archived:
        out = [t for t in out if t["status"] != "archived"]
    return out


def board(cfg: Optional[Config] = None) -> dict:
    """The whole board grouped into ordered columns, with per-status + per-assignee counts."""
    cfg = cfg or get_config()
    tasks = list_tasks(cfg, include_archived=False)
    cols = {s: [] for s in BOARD_ORDER}
    other: list[dict] = []
    for t in tasks:
        if t["status"] in cols:
            cols[t["status"]].append(t)
        else:
            other.append(t)
    # newest first within a column
    for s in cols:
        cols[s].sort(key=lambda t: t["created_ms"] or 0, reverse=True)
    by_assignee: dict[str, int] = {}
    for t in tasks:
        if t["assignee"]:
            by_assignee[t["assignee"]] = by_assignee.get(t["assignee"], 0) + 1
    return {
        "available": available(cfg),
        "columns": [{"status": s, "tasks": cols[s]} for s in BOARD_ORDER],
        "counts": {s: len(cols[s]) for s in BOARD_ORDER},
        "by_assignee": by_assignee,
        "total": len(tasks),
        "other": other,                       # any unexpected status, so nothing is hidden silently
    }


def get_task(task_id: str, cfg: Optional[Config] = None) -> Optional[dict]:
    """One task with its full body, comments, run history, events, and dependency links."""
    cfg = cfg or get_config()
    conn = _connect(cfg)
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        t = _task_row(row)
        t["body"] = row["body"]
        t["result"] = row["result"]
        t["workspace_kind"] = row["workspace_kind"]
        t["comments"] = [
            {"author": c["author"], "body": c["body"], "created_ms": _ms(c["created_at"])}
            for c in conn.execute(
                "SELECT author, body, created_at FROM task_comments "
                "WHERE task_id = ? ORDER BY created_at", (task_id,))
        ]
        t["runs"] = [
            {"id": r["id"], "profile": r["profile"], "status": r["status"],
             "outcome": r["outcome"], "summary": r["summary"], "error": r["error"],
             "started_ms": _ms(r["started_at"]), "ended_ms": _ms(r["ended_at"])}
            for r in conn.execute(
                "SELECT id, profile, status, outcome, summary, error, started_at, ended_at "
                "FROM task_runs WHERE task_id = ? ORDER BY started_at", (task_id,))
        ]
        t["events"] = [
            {"kind": e["kind"], "payload": e["payload"], "created_ms": _ms(e["created_at"])}
            for e in conn.execute(
                "SELECT kind, payload, created_at FROM task_events "
                "WHERE task_id = ? ORDER BY id DESC LIMIT 40", (task_id,))
        ]
        t["children"] = [r["child_id"] for r in conn.execute(
            "SELECT child_id FROM task_links WHERE parent_id = ?", (task_id,))]
        t["parents"] = [r["parent_id"] for r in conn.execute(
            "SELECT parent_id FROM task_links WHERE child_id = ?", (task_id,))]
        return t
    finally:
        conn.close()


# ── write path: delegate by shelling the locked-down privileged wrapper ──────────────────────────

class KanbanError(RuntimeError):
    """A delegation write failed (wrapper missing, validation, or non-zero exit)."""


def _run_wrapper(args: list[str]) -> dict:
    """Invoke the root-owned wrapper via sudo. The wrapper itself whitelists actions + validates
    every arg before it ever reaches `docker exec` — this side passes only structured, escaped args.
    """
    wrapper = _WRAPPER
    if not Path(wrapper).exists():
        raise KanbanError(
            f"delegation wrapper not installed at {wrapper} — run deploy/hermes/install-kanban.sh")
    cmd = ["sudo", "-n", wrapper, *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as e:
        raise KanbanError("delegation wrapper timed out") from e
    except OSError as e:
        raise KanbanError(f"cannot invoke delegation wrapper: {e}") from e
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise KanbanError(msg)
    out = (proc.stdout or "").strip()
    try:
        return json.loads(out) if out else {}
    except json.JSONDecodeError:
        return {"raw": out}


def create_task(title: str, body: str = "", assignee: str = "", created_by: str = "dtm-ai",
                tenant: str = "", idempotency_key: str = "",
                cfg: Optional[Config] = None) -> dict:
    """Delegate: create a board task (optionally pre-assigned to a specialist profile).

    The gateway dispatcher then promotes it to `ready` and spawns the assigned profile's worker.
    Returns the created task summary (incl. its id). Raises KanbanError on failure.
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("task title required")
    if assignee:
        _safe(assignee)                       # profile-name shape; wrapper re-validates server-side
    args = ["create", "--title", title]
    if body.strip():
        args += ["--body", body]
    if assignee:
        args += ["--assignee", assignee]
    if tenant.strip():
        args += ["--tenant", tenant]
    if idempotency_key.strip():
        args += ["--idempotency-key", idempotency_key]
    args += ["--created-by", created_by or "dtm-ai"]
    created = _run_wrapper(args)
    # Kick a dispatcher pass so an assigned task starts running NOW rather than waiting for the
    # gateway's poll. Best-effort: if it fails, the gateway dispatcher still picks it up.
    if assignee:
        try:
            _run_wrapper(["dispatch", "--max", "8"])
        except KanbanError:
            pass
    return created


def dispatch(cfg: Optional[Config] = None) -> dict:
    """Force one dispatcher pass (reclaim stale, promote ready, spawn workers). Idempotent."""
    return _run_wrapper(["dispatch", "--max", "8"])


def archive_task(task_id: str, cfg: Optional[Config] = None) -> dict:
    """Archive a finished task (clears it from the active board)."""
    task_id = (task_id or "").strip()
    if not task_id:
        raise ValueError("task_id required")
    return _run_wrapper(["archive", task_id])


def assign_task(task_id: str, profile: str, cfg: Optional[Config] = None) -> dict:
    """Re/assign an existing task to a specialist profile (or 'none' to unassign)."""
    task_id = (task_id or "").strip()
    if not task_id:
        raise ValueError("task_id required")
    if profile != "none":
        _safe(profile)
    return _run_wrapper(["assign", task_id, profile])


def _wrapper_cmd_preview(args: list[str]) -> str:
    """For audit detail — the exact argv we'd pass (escaped), never the raw shell."""
    return " ".join(shlex.quote(a) for a in args)
