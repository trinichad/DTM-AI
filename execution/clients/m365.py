"""Microsoft Graph client (D-32) — delegated token from m365_auth.

Built via the ClientFactory with a token_source closure over cfg, so it always uses a fresh
(auto-refreshed) access token. Read paths are bounded by scopes.READ_SCOPES['m365']; writes
(D-40) exist only as post()/patch() reached through scopes.scoped_write from an owner-approved
CATEGORY=write tool, bounded by scopes.WRITE_SCOPES['m365'].
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ._http import HttpError, http_json


class M365Client:
    def __init__(self, token_source: Callable[[], str], *, transport: Callable = http_json,
                 base: str = "https://graph.microsoft.com/v1.0") -> None:
        self._token = token_source
        self._t = transport
        self.base = base.rstrip("/")

    @staticmethod
    def _bad_path(path: str) -> bool:
        return not isinstance(path, str) or not path.startswith("/") or "://" in path or ".." in path

    def _url(self, path: str) -> str:
        """A '/beta/...' path is a DELIBERATE per-call opt-in to the Graph beta endpoint (D-60 —
        some APIs, e.g. per-user MFA `authentication/requirements`, only exist there). The scopes
        allowlist validates the path with the prefix stripped, so beta never widens the surface."""
        if path.startswith("/beta/") and self.base.endswith("/v1.0"):
            return self.base[:-len("v1.0")] + "beta" + path[len("/beta"):]
        return f"{self.base}{path}"

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        if self._bad_path(path):
            return {"error": "path must be a simple '/...' Graph path"}
        headers = {"Authorization": f"Bearer {self._token()}"}
        # $count/$search/advanced queries need this header; harmless otherwise
        headers["ConsistencyLevel"] = "eventual"
        _s, data = self._t("GET", self._url(path), headers=headers, params=params or None)
        return data

    # Writes (D-40): only reachable through scopes.scoped_write from an owner-approved
    # CATEGORY=write tool. Graph must have write scopes consented (M365_SCOPES + re-sign-in),
    # else it answers 403 and the tool fails closed.
    def post(self, path: str, body: Optional[dict] = None) -> Any:
        return self._write("POST", path, body)

    def patch(self, path: str, body: Optional[dict] = None) -> Any:
        return self._write("PATCH", path, body)

    def delete(self, path: str, body: Optional[dict] = None) -> Any:
        """DELETE (D-65) — reached only via scopes.scoped_delete from an owner-approved write tool,
        bounded by scopes.DELETE_SCOPES['m365']."""
        if self._bad_path(path):
            return {"error": "path must be a simple '/...' Graph path"}
        headers = {"Authorization": f"Bearer {self._token()}"}
        _s, data = self._t("DELETE", self._url(path), headers=headers)
        return {"ok": True, "status": _s} if data is None else data

    def _write(self, method: str, path: str, body: Optional[dict]) -> Any:
        if self._bad_path(path):
            return {"error": "path must be a simple '/...' Graph path"}
        headers = {"Authorization": f"Bearer {self._token()}"}
        _s, data = self._t(method, self._url(path), headers=headers, json_body=body or {})
        return data if data is not None else {"ok": True, "status": _s}  # 204 No Content

    def probe(self) -> dict[str, Any]:
        try:
            org = self.get("/organization") or {}
            rows = org.get("value") if isinstance(org, dict) else None
            name = (rows[0].get("displayName") if rows else None) or "tenant"
            return {"ok": True, "detail": f"signed in; tenant: {name}"}
        except HttpError as e:
            hint = " (token expired or scope not consented)" if e.status in (401, 403) else ""
            return {"ok": False, "detail": f"Graph HTTP {e.status}{hint}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": str(e)[:160]}


def build_m365(cfg, tenant: str) -> "M365Client":
    """Build a Graph client for ONE managed client (D-33). Fail-closed if that client isn't signed in,
    or if no specific client is bound (M365 is per-client — '*' has no single token)."""
    from ..core import m365_auth
    from ..core.credentials import MissingCredential
    if (tenant or "").strip() in ("", "*"):
        raise MissingCredential("Microsoft 365 is per-client — select a specific client first")
    if not m365_auth.is_connected(cfg, tenant):
        raise MissingCredential(
            f"Microsoft 365: client '{tenant}' is not signed in — connect it on the M365 card")
    return M365Client(m365_auth.token_source(cfg, tenant))
