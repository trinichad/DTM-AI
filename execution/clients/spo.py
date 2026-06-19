"""SharePoint Online admin client (D-89) — CSOM ProcessQuery with a HARD one-method allowlist.

"Site-collection administrator" is a SharePoint concept Microsoft Graph does not expose. The
supported programmatic channel is SharePoint CSOM: an XML `ProcessQuery` POST to the tenant admin
endpoint `https://<tenant>-admin.sharepoint.com/_vti_bin/client.svc/ProcessQuery`, authenticated
with a token whose audience is that admin host (minted from the client's `spo` sign-in — D-89).

Security model (SOP: sharepoint-admin.md), mirroring the EXO cmdlet allowlist (D-41):
  - The client exposes exactly ONE mutating operation, `set_site_admin` — there is no generic
    "run any CSOM" surface. Widening it is a deliberate, hand-reviewed code change.
  - CSOM is transactional: ProcessQuery returns a JSON array whose element carries `ErrorInfo`
    (non-null ⇒ failure). A clean response is therefore a positive confirmation of success, not a
    fire-and-forget — so the write is "verified" by the API's own contract.
  - Built per (spo, tenant) and fail-closed when that client's SharePoint isn't signed in.
  - The transport is injectable so the request/response logic is unit-testable with no network.
"""
from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from typing import Any, Callable, Optional
from xml.sax.saxutils import escape as _xml_escape

from ._http import HttpError

# The CSOM type id of Microsoft.Online.SharePoint.TenantAdministration.Tenant — the object whose
# SetSiteAdmin(siteUrl, loginName, isSiteAdmin) method makes a user a site-collection administrator
# (confirmed against MS Learn dn140313; same op as PowerShell Set-SPOUser -IsSiteCollectionAdmin).
# CSOM parameters are positional, so the wire format is unaffected by the param's name.
_TENANT_TYPE_ID = "{268004ae-ef6b-4e9b-8425-127220d84719}"
_CSOM_PATH = "/_vti_bin/client.svc/ProcessQuery"
_UA = "MSP-AI-SPO/1.0"


# transport signature: (method, url, headers, body_text) -> (status:int, data:Any)
def csom_post(method: str, url: str, headers: Optional[dict] = None, body: Optional[str] = None,
              *, timeout: float = 60.0) -> tuple[int, Any]:
    """Default transport: send a CSOM XML body (or a bare GET) and parse the JSON response.
    ProcessQuery answers JSON even though the request is XML."""
    import json
    data = body.encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=dict(headers or {}),
                                 method=method.upper())
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310
            raw = resp.read()
            text = raw.decode("utf-8", "replace") if raw else ""
            if not text:
                return resp.status, None
            try:
                return resp.status, json.loads(text)
            except json.JSONDecodeError:
                raise HttpError(resp.status, f"expected JSON from SharePoint, got: "
                                             f"{' '.join(text.split())[:200]}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        raise HttpError(e.code, raw) from e


def login_claim(upn: str) -> str:
    """A member user's site-admin login claim, e.g. user@x.com → i:0#.f|membership|user@x.com."""
    upn = (upn or "").strip()
    return upn if upn.startswith("i:0#") else f"i:0#.f|membership|{upn}"


class SPOClient:
    def __init__(self, token_source: Callable[[], str], admin_host: str, *,
                 my_host: str = "", transport: Callable = csom_post) -> None:
        self._token = token_source
        self.admin_host = (admin_host or "").strip().lower()
        self.my_host = (my_host or "").strip().lower()
        self._t = transport

    def _admin_url(self, path: str) -> str:
        return f"https://{self.admin_host}{path}"

    @staticmethod
    def _set_site_admin_xml(site_url: str, login_name: str, is_admin: bool) -> str:
        return (
            '<Request xmlns="http://schemas.microsoft.com/sharepoint/clientquery/2009" '
            'SchemaVersion="15.0.0.0" LibraryVersion="16.0.0.0" ApplicationName="MSP AI">'
            '<Actions><Method Name="SetSiteAdmin" Id="1" ObjectPathId="2"><Parameters>'
            f'<Parameter Type="String">{_xml_escape(site_url)}</Parameter>'
            f'<Parameter Type="String">{_xml_escape(login_name)}</Parameter>'
            f'<Parameter Type="Boolean">{"true" if is_admin else "false"}</Parameter>'
            '</Parameters></Method></Actions>'
            f'<ObjectPaths><Constructor Id="2" TypeId="{_TENANT_TYPE_ID}" /></ObjectPaths></Request>'
        )

    @staticmethod
    def _csom_error(data: Any) -> Optional[str]:
        """ProcessQuery returns a JSON array; an element's non-null ErrorInfo means failure."""
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("ErrorInfo"):
                    info = item["ErrorInfo"]
                    if isinstance(info, dict):
                        return str(info.get("ErrorMessage") or info.get("ErrorTypeName") or info)
                    return str(info)
        return None

    def set_site_admin(self, site_url: str, login_name: str, is_admin: bool = True) -> dict:
        """Make `login_name` a site-collection administrator of the site at `site_url`
        (the API behind `Set-SPOUser -IsSiteCollectionAdmin`). Returns {"ok": True} on a clean
        CSOM response, else {"error": ...} — never raises for an expected SharePoint error."""
        site_url = (site_url or "").strip()
        login_name = login_name.strip() if isinstance(login_name, str) else ""
        if not site_url.startswith("https://"):
            return {"error": "site_url must be an absolute https:// OneDrive/site URL"}
        if not login_name:
            return {"error": "a login name (user) is required"}
        if not self.admin_host:
            return {"error": "no SharePoint admin host for this client — reconnect SharePoint"}
        body = self._set_site_admin_xml(site_url, login_name, is_admin)
        headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "text/xml",
                   "Accept": "application/json", "User-Agent": _UA}
        try:
            _s, data = self._t("POST", self._admin_url(_CSOM_PATH), headers, body)
        except HttpError as e:
            hint = " (SharePoint/Global admin role + SharePoint sign-in needed?)" \
                if e.status in (401, 403) else ""
            return {"error": f"SharePoint HTTP {e.status}{hint}: {e.body[:300]}"}
        err = self._csom_error(data)
        if err:
            return {"error": f"SharePoint refused: {err}"}
        return {"ok": True, "site_url": site_url, "login_name": login_name,
                "is_admin": bool(is_admin)}

    def probe(self) -> dict[str, Any]:
        """Prove the admin-host token works with a tiny authenticated read."""
        headers = {"Authorization": f"Bearer {self._token()}",
                   "Accept": "application/json;odata=nometadata", "User-Agent": _UA}
        try:
            _s, data = self._t("GET", self._admin_url("/_api/web?$select=Title"), headers, None)
        except HttpError as e:
            hint = " (re-sign-in / admin role needed?)" if e.status in (401, 403) else ""
            return {"ok": False, "detail": f"SharePoint HTTP {e.status}{hint}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": str(e)[:160]}
        title = data.get("Title") if isinstance(data, dict) else None
        return {"ok": True, "detail": f"SharePoint admin ok ({self.admin_host}"
                                      + (f"; {title}" if title else "") + ")"}


def build_spo(cfg, tenant: str) -> "SPOClient":
    """Build a SharePoint admin client for ONE managed client (D-89). Fail-closed if that client's
    SharePoint isn't signed in (or the session is '*' — SPO is per-client like Graph/EXO)."""
    from ..core import m365_auth
    from ..core.credentials import MissingCredential
    if (tenant or "").strip() in ("", "*"):
        raise MissingCredential("SharePoint Online is per-client — pick a specific client first")
    if not m365_auth.is_connected(cfg, tenant, service="spo"):
        raise MissingCredential(
            f"SharePoint Online: client '{tenant}' is not signed in — connect SharePoint on the "
            f"M365 card (a separate sign-in from Graph, needs a SharePoint/Global admin)")
    admin_host = m365_auth.spo_admin_host(cfg, tenant)
    if not admin_host:
        raise MissingCredential(
            f"SharePoint resource unknown for '{tenant}' — reconnect SharePoint on the M365 card")
    return SPOClient(m365_auth.token_source(cfg, tenant, service="spo"), admin_host,
                     my_host=m365_auth.spo_my_host(cfg, tenant))
