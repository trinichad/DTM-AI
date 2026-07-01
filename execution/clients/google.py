"""Google Workspace admin client (D-118) — delegated token from gws_auth.

Built per managed client via build_gws, over a token_source closure so it always uses a fresh
(auto-refreshed) access token. Read paths are bounded by scopes.READ_SCOPES['gws']; writes exist
only as post()/put()/patch()/delete() reached through scopes.scoped_write / scoped_delete from an
owner-approved CATEGORY=write tool, bounded by WRITE_SCOPES / DELETE_SCOPES['gws'].

Google spans several API hosts, so the host is chosen from a FIXED map keyed on the path's leading
segment — never from the path itself — so a path can't escape to an arbitrary host. Phase 1 uses
the Admin SDK Directory API (admin.googleapis.com); the other hosts are pre-wired for later phases
but only reachable once their prefixes are added to the scopes allowlist.
SOP: architecture/google-workspace.md.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ._http import HttpError, http_json

# (path-prefix, host). First match wins; the first entry is the default. Adding a new Google API on
# a new host = add a row here AND allowlist its path prefix in scopes.py.
_HOSTS: tuple[tuple[str, str], ...] = (
    ("/admin/", "https://admin.googleapis.com"),          # Admin SDK: Directory, Reports, DataTransfer
    ("/drive/", "https://www.googleapis.com"),            # Drive API (Shared Drives, files)
    ("/gmail/", "https://gmail.googleapis.com"),          # Gmail per-user settings
    ("/apps/licensing/", "https://licensing.googleapis.com"),  # Enterprise License Manager
    ("/v1/", "https://cloudidentity.googleapis.com"),     # Cloud Identity (groups/memberships)
)
_DEFAULT_HOST = _HOSTS[0][1]


class GoogleClient:
    def __init__(self, token_source: Callable[[], str], *, transport: Callable = http_json) -> None:
        self._token = token_source
        self._t = transport

    @staticmethod
    def _bad_path(path: str) -> bool:
        return (not isinstance(path, str) or not path.startswith("/")
                or "://" in path or path.startswith("//") or ".." in path)

    def _url(self, path: str) -> str:
        host = _DEFAULT_HOST
        for prefix, h in _HOSTS:
            if path == prefix.rstrip("/") or path.startswith(prefix):
                host = h
                break
        return f"{host}{path}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        if self._bad_path(path):
            return {"error": "path must be a simple '/...' Google API path"}
        _s, data = self._t("GET", self._url(path), headers=self._headers(), params=params or None)
        return data

    # Writes (D-118): only reachable through scopes.scoped_write / scoped_delete from an
    # owner-approved CATEGORY=write tool. Google must have the matching write scope consented,
    # else it answers 403 and the tool fails closed.
    def post(self, path: str, body: Optional[dict] = None) -> Any:
        return self._write("POST", path, body)

    def put(self, path: str, body: Optional[dict] = None) -> Any:
        return self._write("PUT", path, body)

    def patch(self, path: str, body: Optional[dict] = None) -> Any:
        return self._write("PATCH", path, body)

    def _write(self, method: str, path: str, body: Optional[dict]) -> Any:
        if self._bad_path(path):
            return {"error": "path must be a simple '/...' Google API path"}
        _s, data = self._t(method, self._url(path), headers=self._headers(), json_body=body or {})
        return data if data is not None else {"ok": True, "status": _s}   # 204 No Content

    def delete(self, path: str, body: Optional[dict] = None) -> Any:
        if self._bad_path(path):
            return {"error": "path must be a simple '/...' Google API path"}
        _s, data = self._t("DELETE", self._url(path), headers=self._headers())
        return {"ok": True, "status": _s} if data is None else data

    def probe(self) -> dict[str, Any]:
        """Cheap sign-in + admin-rights check: read the customer record for the signed-in admin."""
        try:
            cust = self.get("/admin/directory/v1/customers/my_customer") or {}
            if isinstance(cust, dict) and cust.get("error"):
                return {"ok": False, "detail": str(cust["error"])[:160]}
            dom = (cust.get("customerDomain") if isinstance(cust, dict) else None) or "workspace"
            return {"ok": True, "detail": f"signed in; domain: {dom}"}
        except HttpError as e:
            hint = " (token expired or scope not consented)" if e.status in (401, 403) else ""
            return {"ok": False, "detail": f"Google API HTTP {e.status}{hint}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": str(e)[:160]}


def build_gws(cfg, tenant: str) -> "GoogleClient":
    """Build a Google Workspace client for ONE managed client (D-118). Fail-closed if that client
    isn't signed in, or if no specific client is bound ('*' has no single token)."""
    from ..core import gws_auth
    from ..core.credentials import MissingCredential
    if (tenant or "").strip() in ("", "*"):
        raise MissingCredential("Google Workspace is per-client — select a specific client first")
    if not gws_auth.is_connected(cfg, tenant):
        raise MissingCredential(
            f"Google Workspace: client '{tenant}' is not signed in — connect it on the "
            f"Google Workspace card")
    return GoogleClient(gws_auth.token_source(cfg, tenant))
