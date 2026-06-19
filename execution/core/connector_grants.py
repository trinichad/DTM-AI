"""Owner-approved connector self-extension (D-64).

When the AI needs an Exchange cmdlet that isn't on the built-in connector allowlist, it proposes
a grant (`skills/propose_connector_capability.py`); the owner approves it on a normal approval
card; the grant is persisted HERE and `EXOClient` merges it into its effective allowlist.

This is a SECOND owner gate, never a removed one. Hard floors (can_grant):
  - self-extension can only ever add read|write cmdlets — NEVER destructive (data loss stays
    hand-written behind the D-54 floor);
  - a curated FORBIDDEN denylist blocks the catastrophic cmdlets outright;
  - a cmdlet already built-in can't be re-granted (no widening of curated params);
  - the owner reviews + can revoke every grant.

Stdlib-only. JSON at <vault>/connector_grants.json (0600, git-trackable per I-6).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_lock = threading.Lock()

KNOWN_CONNECTORS = ("exo",)
VALID_KINDS = ("read", "write")

# Cmdlets the self-extension may NEVER add, whatever the owner clicks — data loss, data
# exfiltration, or tampering with the security/audit controls. These stay hand-written only
# (and data deletion additionally behind the D-54 destructive floor). A backstop, not the
# primary control: the owner reviewing each grant is. Matched case-insensitively, and any
# cmdlet whose verb is Remove/Disable on a mailbox/recipient OBJECT is treated with care below.
FORBIDDEN: frozenset = frozenset(x.lower() for x in {
    "Remove-Mailbox", "Remove-MailboxDatabase", "Remove-MailUser", "Remove-User",
    "Remove-RecipientPermission",          # (this one IS hand-written already; keep off self-grant)
    "New-MailboxExportRequest", "New-MailboxImportRequest",
    "New-ComplianceSearch", "New-ComplianceSearchAction",
    "Set-AdminAuditLogConfig", "Set-Mailbox",   # Set-Mailbox is curated built-in; never self-grant
    "New-InboundConnector", "Set-TransportConfig",
})


def _path() -> Path:
    vault = os.environ.get("MSPAI_VAULT_PATH") or str(_PROJECT_ROOT / "vault")
    return Path(vault) / "connector_grants.json"


def _load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError):
        return {}


def _save(data: dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def can_grant(connector: str, cmdlet: str, kind: str = "write") -> tuple[bool, str]:
    """Floor check — returns (allowed, reason). Refuses anything self-extension must never add."""
    connector = (connector or "").strip().lower()
    cmdlet = (cmdlet or "").strip()
    kind = (kind or "").strip().lower()
    if connector not in KNOWN_CONNECTORS:
        return False, f"unknown connector '{connector}' (self-extension supports: " \
                      f"{', '.join(KNOWN_CONNECTORS)})"
    if not cmdlet or " " in cmdlet or "/" in cmdlet:
        return False, "a cmdlet name like 'Remove-MailboxPermission' is required"
    if kind not in VALID_KINDS:
        return False, "kind must be 'read' or 'write' — self-extension can never add a " \
                      "destructive (data-deleting) cmdlet"
    if cmdlet.lower() in FORBIDDEN:
        return False, (f"'{cmdlet}' is on the permanent block-list (data loss / exfiltration / "
                       f"security-control tampering) — it can never be self-granted; a developer "
                       f"must hand-write it if it's genuinely needed")
    # destructive set + already-built-in checks need the connector module
    if connector == "exo":
        from ..clients.exo import ALLOWED_CMDLETS, DESTRUCTIVE_CMDLETS
        if cmdlet in DESTRUCTIVE_CMDLETS:
            return False, f"'{cmdlet}' is a destructive cmdlet — it can never be self-granted"
        if cmdlet in ALLOWED_CMDLETS:
            return False, f"'{cmdlet}' is already available on the connector — nothing to add"
    return True, "ok"


def add(connector: str, cmdlet: str, kind: str, params: Optional[list],
        *, reason: str = "", by: str = "") -> dict[str, Any]:
    """Persist a grant after the floor passes. Returns {'ok': True, ...} or {'ok': False,'error'}."""
    ok, why = can_grant(connector, cmdlet, kind)
    if not ok:
        return {"ok": False, "error": why}
    connector = connector.strip().lower()
    cmdlet = cmdlet.strip()
    clean_params = sorted({str(p).strip() for p in (params or []) if str(p or "").strip()})
    from datetime import datetime, timezone
    entry = {"kind": kind.strip().lower(), "params": clean_params,
             "reason": (reason or "").strip()[:300], "by": by,
             "ts": datetime.now(timezone.utc).isoformat()}
    with _lock:
        data = _load()
        data.setdefault(connector, {})[cmdlet] = entry
        _save(data)
    return {"ok": True, "connector": connector, "cmdlet": cmdlet, **entry}


def grants_for(connector: str) -> tuple[dict[str, str], dict[str, frozenset]]:
    """(cmdlet→kind, cmdlet→param frozenset) for a connector — what EXOClient merges in.
    Destructive/forbidden entries are filtered defensively even if one reached disk."""
    connector = (connector or "").strip().lower()
    cmdlets: dict[str, str] = {}
    params: dict[str, frozenset] = {}
    for cmdlet, e in (_load().get(connector) or {}).items():
        if not isinstance(e, dict):
            continue
        ok, _ = can_grant(connector, cmdlet, e.get("kind", "write"))
        if not ok:
            continue                                   # never serve a now-forbidden grant
        cmdlets[cmdlet] = str(e.get("kind") or "write")
        params[cmdlet] = frozenset(e.get("params") or [])
    return cmdlets, params


def list_all() -> list[dict[str, Any]]:
    out = []
    for connector, entries in _load().items():
        for cmdlet, e in (entries or {}).items():
            if isinstance(e, dict):
                out.append({"connector": connector, "cmdlet": cmdlet, **e})
    return sorted(out, key=lambda g: (g["connector"], g["cmdlet"]))


def revoke(connector: str, cmdlet: str) -> bool:
    connector = (connector or "").strip().lower()
    with _lock:
        data = _load()
        if cmdlet in (data.get(connector) or {}):
            del data[connector][cmdlet]
            if not data[connector]:
                del data[connector]
            _save(data)
            return True
    return False
