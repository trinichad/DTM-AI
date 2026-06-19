"""Capability Console backend — the throttle for graduated autonomy.

Design intent (per owner decision, 2026-06): the platform should NOT be hard-locked to
read-only forever. Instead the owner gets a control panel to open capabilities tool by
tool as trust is earned during testing — ramping toward an agent that can eventually
"work on its own". This module is the data model + policy behind that console.

Defense-in-depth, with the owner holding the throttle:
  Layer 1 (primary)  — this capability policy: per tool {enabled, allow_write, require_approval}
  Layer 2 (backup)   — least-privilege vendor API creds (a read-only key rejects writes upstream)
  Layer 3 (always)   — the SAFETY FLOORS below, which the console cannot turn off

SAFETY FLOORS (non-negotiable; not exposed as toggles in v1):
  - every call is audited (reads included)
  - tenant isolation is enforced regardless of capability settings
  - secrets are never exposed (fingerprints only)
  - destructive-category tools default to allow_write=False AND require_approval=True
  - the self-CODING agent still requires human merge to add a NEW tool (separate from
    toggling an EXISTING tool's runtime capability)

A tool with no stored policy uses safe defaults: enabled per its ENABLED_BY_DEFAULT,
no writes, approval required for any write.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _PROJECT_ROOT / "msp_ai.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS capabilities (
    name             TEXT PRIMARY KEY,   -- tool NAME (or, later, a Hermes toolset id)
    enabled          INTEGER NOT NULL DEFAULT 0,
    allow_write      INTEGER NOT NULL DEFAULT 0,
    require_approval INTEGER NOT NULL DEFAULT 1
);
"""


@dataclass(frozen=True)
class Policy:
    name: str
    enabled: bool
    allow_write: bool
    require_approval: bool


class CapabilityStore:
    """Owns the `capabilities` table. Shares the sqlite file with the audit store."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._path = str(db_path or _DEFAULT_DB)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def get(self, name: str, *, default_enabled: bool) -> Policy:
        with self._lock:
            cur = self._conn.execute(
                "SELECT enabled, allow_write, require_approval FROM capabilities WHERE name=?",
                (name,),
            )
            row = cur.fetchone()
        if row is None:
            # No stored policy -> safe defaults (enabled per the tool, but never writes).
            return Policy(name, default_enabled, allow_write=False, require_approval=True)
        return Policy(
            name,
            enabled=bool(row["enabled"]),
            allow_write=bool(row["allow_write"]),
            require_approval=bool(row["require_approval"]),
        )

    def set(
        self,
        name: str,
        *,
        enabled: Optional[bool] = None,
        allow_write: Optional[bool] = None,
        require_approval: Optional[bool] = None,
    ) -> Policy:
        """Upsert a policy. Only provided fields change; others keep their stored/default value."""
        current = self.get(name, default_enabled=False)
        new = Policy(
            name,
            enabled=current.enabled if enabled is None else enabled,
            allow_write=current.allow_write if allow_write is None else allow_write,
            require_approval=current.require_approval if require_approval is None else require_approval,
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO capabilities(name, enabled, allow_write, require_approval) "
                "VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "enabled=excluded.enabled, allow_write=excluded.allow_write, "
                "require_approval=excluded.require_approval",
                (name, int(new.enabled), int(new.allow_write), int(new.require_approval)),
            )
            self._conn.commit()
        return new

    def all(self) -> dict[str, Policy]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT name, enabled, allow_write, require_approval FROM capabilities"
            )
            return {
                r["name"]: Policy(r["name"], bool(r["enabled"]), bool(r["allow_write"]),
                                  bool(r["require_approval"]))
                for r in cur.fetchall()
            }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
