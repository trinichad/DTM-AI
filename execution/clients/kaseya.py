"""Kaseya VSA 9.x REST client. Reads are unbounded GETs; WRITES (D-69) are bounded by a HARD
endpoint allow-list (WRITE_RULES / DESTRUCTIVE_RULES) — mirroring the EXO cmdlet allowlist.

Auth (verified working against vsa.example.com): plain Basic `base64(user:pass)` →
`GET /api/v1.0/auth` → `Result.Token`, then `Authorization: Bearer <token>` for ~20 min.
A pre-issued KASEYA_TOKEN (Bearer) is used directly if present, skipping /auth.

NOTE: the `msp-ai-ops` build's token-id/token-secret-against-/vsa/api/v2 scheme never actually
authenticated (returned the VSA web "Whoops." page / ResponseCode 4010001). This client uses the
USER/PASS → token-exchange flow that Kaseya Link proved out on the live tenant.
"""
from __future__ import annotations

import base64
import re
import time
from typing import Any, Callable, Optional

from ._http import HttpError, http_json

# Write surface (D-69) — (METHOD, path-regex). Anything not matched is refused before any HTTP,
# so even a promoted AI draft can't reach an arbitrary write. Verified vs the VSA REST v1.0 ref.
# Structural DELETEs live in their OWN list, reachable only via write_destructive() from a
# hand-written CATEGORY=destructive skill.
WRITE_RULES: tuple[tuple[str, str], ...] = (
    ("PUT", r"^/assetmgmt/alarms/\d+/close$"),
    ("PUT", r"^/automation/agentprocs/\d+/\d+/runnow$"),
    ("PUT", r"^/automation/agentprocs/\d+/\d+/schedule$"),
    ("DELETE", r"^/automation/agentprocs/\d+/\d+$"),                 # cancel a scheduled proc
    ("PUT", r"^/assetmgmt/audit/(baseline|latest|sysinfo)/\d+/(runnow|schedule)$"),
    ("PUT", r"^/assetmgmt/patch/\d+/scannow$"),                      # patch SCAN (not /schedule = deploy)
    ("PUT", r"^/assetmgmt/audit/[^/]+/hardware/purchaseandwarrantyexpire$"),
    ("POST", r"^/automation/servicedesktickets/\d+/notes$"),
    ("PUT", r"^/automation/servicedesktickets/\d+/status/\d+$"),
    ("PUT", r"^/automation/servicedesktickets/\d+/priority/\d+$"),
    ("PUT", r"^/automation/servicedesktickets/\d+/customfields/\d+$"),
    ("PUT", r"^/automation/servicedesks/assign/\d+/\d+$"),
    ("POST", r"^/system/orgs$"),
    ("PUT", r"^/system/orgs/\d+$"),
    ("POST", r"^/system/orgs/\d+/machinegroups$"),                  # create is org-scoped (POST)
    ("PUT", r"^/system/machinegroups/\d+$"),                        # update is by MG id (not org-scoped)
)
DESTRUCTIVE_RULES: tuple[tuple[str, str], ...] = (
    ("DELETE", r"^/system/orgs/\d+$"),
    ("DELETE", r"^/system/machinegroups/\d+$"),                     # delete is by MG id (VSA 9 Swagger)
    ("DELETE", r"^/assetmgmt/assets/[^/]+$"),
)


def _matches(rules: tuple, method: str, path: str) -> bool:
    base = (path or "").split("?", 1)[0]
    return any(method.upper() == m and re.match(rx, base) for m, rx in rules)


class KaseyaClient:
    def __init__(
        self, base_url: str, username: Optional[str] = None, password: Optional[str] = None,
        *, token: Optional[str] = None, verify_tls: bool = True,
        transport: Callable = http_json,
    ) -> None:
        self.base = base_url.rstrip("/") + "/api/v1.0"
        self.username = username
        self.password = password
        self.verify = verify_tls
        self._t = transport
        self.token = token
        self.static_token = bool(token)
        self.token_expires = float("inf") if token else 0.0

    # ── auth ──
    def login(self) -> None:
        if self.static_token:
            return
        if not (self.username and self.password):
            raise RuntimeError("KASEYA_USER/KASEYA_PASS required when no KASEYA_TOKEN is set")
        creds = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        _status, data = self._t("GET", f"{self.base}/auth",
                                headers={"Authorization": f"Basic {creds}"})
        result = (data or {}).get("Result") or {}
        self.token = result.get("Token")
        self.token_expires = time.time() + 20 * 60
        if not self.token:
            raise RuntimeError(f"Kaseya login failed: {data}")

    def _auth_header(self) -> dict[str, str]:
        if not self.token or time.time() > self.token_expires:
            self.login()
        return {"Authorization": f"Bearer {self.token}"}

    # ── requests ──
    def get(self, path: str, params: Optional[dict] = None) -> Any:
        _status, data = self._t("GET", f"{self.base}{path}",
                                headers=self._auth_header(), params=params)
        return data

    # ── writes (D-69) — bounded by WRITE_RULES; never reaches a destructive DELETE ──
    def write(self, method: str, path: str, body: Optional[Any] = None) -> Any:
        """Run ONE allow-listed write. Refuses anything not in WRITE_RULES (incl. every
        destructive rule) before any HTTP. Returns the body, or {"error": ...}."""
        method = (method or "").upper()
        if _matches(DESTRUCTIVE_RULES, method, path):
            return {"error": f"{method} {path} is a destructive operation — not reachable from "
                             f"the normal write path"}
        if not _matches(WRITE_RULES, method, path):
            return {"error": f"{method} {path} is not in the Kaseya write allow-list"}
        return self._write(method, path, body)

    def write_destructive(self, method: str, path: str, body: Optional[Any] = None) -> Any:
        """Run ONE destructive structural DELETE (D-69). Callable ONLY from a hand-written
        CATEGORY=destructive skill — dispatch's floor forces a fresh owner approval per run."""
        method = (method or "").upper()
        if not _matches(DESTRUCTIVE_RULES, method, path):
            return {"error": f"{method} {path} is not in the Kaseya destructive allow-list"}
        return self._write(method, path, body)

    def _write(self, method: str, path: str, body: Optional[Any]) -> Any:
        try:
            status, data = self._t(method.upper(), f"{self.base}{path}",
                                   headers=self._auth_header(),
                                   json_body=body if body is not None else None)
        except HttpError as e:
            return {"error": f"Kaseya HTTP {e.status}: {str(e.body)[:300]}"}
        # VSA 9 returns the literal string "Error": "None" on SUCCESS — only a non-"none" Error
        # is a real failure (mirrors _kaseya_common._envelope_error).
        err = data.get("Error") if isinstance(data, dict) else None
        if err and str(err).strip().lower() not in ("", "none", "null"):
            return {"error": str(err)}
        return data if data is not None else {"ok": True, "status": status}

    def get_all(self, path: str, params: Optional[dict] = None, page_size: int = 100) -> list[dict]:
        params = dict(params or {})
        params["$top"] = page_size
        out: list[dict] = []
        skip = 0
        while True:
            params["$skip"] = skip
            data = self.get(path, params) or {}
            page = data.get("Result") or []
            out.extend(page)
            total = data.get("TotalRecords")
            if total is not None and len(out) >= int(total):
                break
            if len(page) < page_size:
                break
            skip += len(page)
        return out

    # ── read convenience ──
    def get_assets(self, filters: Optional[str] = None) -> list[dict]:
        return self.get_all("/assetmgmt/assets", {"$filter": filters} if filters else {})

    def get_asset(self, asset_id: str) -> dict:
        needle = str(asset_id)
        for a in self.get_all("/assetmgmt/assets"):
            if needle in (str(a.get("AgentId")), str(a.get("AgentGuid")), str(a.get("AssetId"))):
                return a
        return {}

    def get_agents(self, filters: Optional[str] = None) -> list[dict]:
        """All managed AGENTS (machines with the Kaseya agent installed) — the machine-group
        view. Distinct from /assetmgmt/assets (asset-management records): a machine can be a
        managed agent here without having an asset record, so this is the authoritative list for
        'which machines are in group X'. Mirrors the proven Kaseya Link client."""
        return self.get_all("/assetmgmt/agents", {"$filter": filters} if filters else {})

    def get_agent(self, agent_id: str) -> dict:
        return (self.get(f"/assetmgmt/agents/{agent_id}") or {}).get("Result", {})

    def get_orgs(self) -> list[dict]:
        return (self.get("/system/orgs") or {}).get("Result", [])

    def probe(self) -> dict[str, Any]:
        # Validate against assets (read-only bridge accounts can read these but are often
        # denied /system/orgs — a 403 there is a scope limit, not an auth failure). Mirrors
        # the proven Kaseya Link probe.
        data = self.get("/assetmgmt/assets", {"$top": 1}) or {}
        rows = data.get("Result") or []
        return {"ok": True, "detail": f"auth ok; /assetmgmt/assets returned {len(rows)} row(s)"}
