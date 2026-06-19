"""Microsoft 365 / Graph delegated device-code auth — PER CLIENT (D-32, D-33).

MSP model: ONE multi-tenant public-client Entra app (its CLIENT_ID + scopes are global config),
but each managed client signs in SEPARATELY with that client's own admin (password + MFA at
microsoft.com/devicelogin). Each client's refresh token is stored UNDER THAT CLIENT, so one
client's Graph access never bleeds into another's.

Per-client token store (D-37, split): the SECRETS (refresh_token + cached access_token) live in
the client's encrypted CredVault entry 'm365_oauth' (credentials.enc — visible in the Memory tab
as fingerprints, rotated on every refresh); non-secret status/health stays in the plain sidecar
<vault>/clients/<tenant>/m365.json (0600) — {tenant_id, connected, refresh_fp, access_expires,
obtained, last_refresh, last_error*} — so connection listings never need a vault decrypt. While
the vault is locked/uninitialized the secrets fall back inline into the sidecar (pre-D-37 posture)
and migrate into the vault on the first unlocked use. The access token (a Graph JWT) is re-minted
from the refresh token near expiry and the rotated refresh token is written back. Fail-closed
(Rule #8): a client that isn't signed in builds no client; a locked vault raises an honest
"unlock the vault" error rather than degrading.
SOP: architecture/m365-graph.md.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets as _secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from .config import Config
from .credentials import MissingCredential
from .credvault import VaultLocked, get_credvault

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DEFAULT_TENANT = "organizations"               # device sign-in works against any work/school tenant
DEFAULT_SCOPES = "offline_access openid profile User.Read.All"

# Microsoft's own first-party PUBLIC client "Microsoft Graph Command Line Tools" — the same app
# `Connect-MgGraph` uses. It supports device-code + delegated Graph scopes and exists in every
# tenant, so the owner does NOT have to register their own app: each client admin just signs in
# (and consents to the requested scopes once). An owner who prefers their own app can still set
# M365_CLIENT_ID to override this default.
MS_GRAPH_CLI_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"

CLIENT_KEY = "M365_CLIENT_ID"                  # optional override; defaults to the built-in app
TENANT_KEY = "M365_TENANT"                     # global default for the device-code endpoint
SCOPES_KEY = "M365_SCOPES"                     # global delegated scopes

# ── services (D-41): ONE device-code machinery, multiple per-client connections ──
# "m365" = Microsoft Graph. "exo" = the Exchange Online admin REST API, which needs a DIFFERENT
# token audience (outlook.office365.com) issued to Microsoft's first-party "Microsoft Exchange
# REST API Based Powershell" public app — the same one `Connect-ExchangeOnline` uses.
EXO_PS_CLIENT_ID = "fb78d390-0c51-40cd-8e17-fdbfab77341b"
EXO_DEFAULT_SCOPES = "https://outlook.office365.com/.default offline_access openid profile"

# "spo" = SharePoint Online admin (CSOM) — a THIRD audience (D-89). Unlike Graph/EXO, SharePoint's
# resource is PER TENANT (https://<tenant>-admin.sharepoint.com), so its scope is computed at
# sign-in from the host we discover via the client's existing Graph token (see sharepoint_hosts).
# Microsoft's first-party "SharePoint Online Management Shell" public app (what Connect-SPOService
# uses) supports device-code + SharePoint admin delegated permissions — no app registration needed.
SPO_MGMT_CLIENT_ID = "9bc3ab49-b65d-410a-85ad-de819febfddc"

_SERVICES: dict[str, dict] = {
    "m365": {"name": "Microsoft 365", "file": "m365.json", "label": "m365_oauth",
             "client_key": CLIENT_KEY, "scopes_key": SCOPES_KEY,
             "default_app": MS_GRAPH_CLI_CLIENT_ID, "default_scopes": DEFAULT_SCOPES},
    "exo": {"name": "Exchange Online", "file": "exo.json", "label": "exo_oauth",
            "client_key": "EXO_CLIENT_ID", "scopes_key": "EXO_SCOPES",
            "default_app": EXO_PS_CLIENT_ID, "default_scopes": EXO_DEFAULT_SCOPES},
    "spo": {"name": "SharePoint Online", "file": "spo.json", "label": "spo_oauth",
            "client_key": "SPO_CLIENT_ID", "scopes_key": "SPO_SCOPES",
            "default_app": SPO_MGMT_CLIENT_ID, "default_scopes": ""},  # scope computed per tenant
}


def _svc(service: str) -> dict:
    s = _SERVICES.get(service)
    if s is None:
        raise ValueError(f"unknown M365 service '{service}'")
    return s


def _client_id(cfg: Config, service: str = "m365") -> str:
    s = _svc(service)
    return (cfg.get(s["client_key"]) or s["default_app"]).strip() or s["default_app"]
_SKEW_S = 300
_UA = "MSP-AI-M365/1.0"

_refresh_lock = threading.Lock()
_flows: dict[str, dict] = {}
_flows_lock = threading.Lock()


def _safe_tenant(tenant: str) -> str:
    cleaned = _SAFE.sub("_", tenant or "").strip("._")
    return cleaned or "_unknown"


def _clients_root(cfg: Config) -> Path:
    return Path(cfg.get("MSPAI_VAULT_PATH") or (_PROJECT_ROOT / "vault")) / "clients"


def _store_path(cfg: Config, tenant: str, service: str = "m365") -> Path:
    return _clients_root(cfg) / _safe_tenant(tenant) / _svc(service)["file"]


def _scopes(cfg: Config, service: str = "m365") -> str:
    s = _svc(service)
    return (cfg.get(s["scopes_key"]) or s["default_scopes"]).strip() or s["default_scopes"]


# ── SharePoint Online: per-tenant resource discovery + dynamic scope (D-89) ──
def _spo_scope(admin_host: str) -> str:
    return f"https://{admin_host}/.default offline_access openid profile"


def _hosts_from_root_weburl(web_url: str) -> dict[str, str]:
    """Derive {root_host, admin_host, my_host} from the tenant's root site webUrl
    (https://<tenant>.sharepoint.com). Raises if it isn't a *.sharepoint.com host."""
    host = urllib.parse.urlparse(web_url or "").netloc.lower()
    if not host.endswith(".sharepoint.com") or host.count(".") < 2:
        raise MissingCredential(
            f"could not determine the SharePoint hostname (webUrl={web_url!r}) — is SharePoint "
            f"provisioned for this tenant?")
    base = host[: -len(".sharepoint.com")]
    return {"root_host": host, "admin_host": f"{base}-admin.sharepoint.com",
            "my_host": f"{base}-my.sharepoint.com"}


def sharepoint_hosts(cfg: Config, tenant: str) -> dict[str, str]:
    """Discover {root_host, admin_host, my_host} for a client from its EXISTING Graph sign-in.
    SharePoint's token audience is per-tenant, so the hostname is learned from Graph (/sites/root)
    rather than guessed from the UPN domain (vanity domains lie). Graph must be connected."""
    from ..clients.m365 import M365Client
    if not is_connected(cfg, tenant, "m365"):
        raise MissingCredential(
            f"connect Microsoft 365 (Graph) for client '{tenant}' first — the SharePoint "
            f"hostname is discovered from Graph")
    token = ensure_fresh(cfg, tenant, service="m365")
    root = M365Client(lambda: token).get("/sites/root", {"$select": "webUrl"})
    web = (root or {}).get("webUrl") if isinstance(root, dict) else ""
    return _hosts_from_root_weburl(web)


def spo_admin_host(cfg: Config, tenant: str) -> str:
    """The SharePoint admin host stored at sign-in for a connected client ('' if not set)."""
    return str(_read_side(cfg, tenant, "spo").get("admin_host") or "")


def spo_my_host(cfg: Config, tenant: str) -> str:
    return str(_read_side(cfg, tenant, "spo").get("my_host") or "")


def _refresh_scope(cfg: Config, tenant: str, service: str) -> str:
    """The scope used when redeeming a refresh token. For SharePoint it is rebuilt per tenant from
    the stored admin host (a missing host means the client must reconnect SharePoint)."""
    if service == "spo":
        host = spo_admin_host(cfg, tenant)
        if not host:
            raise MissingCredential(
                f"SharePoint resource unknown for '{tenant}' — reconnect SharePoint on the M365 card")
        return _spo_scope(host)
    return _scopes(cfg, service)


def _global_tenant(cfg: Config) -> str:
    return (cfg.get(TENANT_KEY) or DEFAULT_TENANT).strip() or DEFAULT_TENANT


def _devicecode_url(tenant: str) -> str:
    return f"https://login.microsoftonline.com/{urllib.parse.quote(tenant)}/oauth2/v2.0/devicecode"


def _token_url(tenant: str) -> str:
    return f"https://login.microsoftonline.com/{urllib.parse.quote(tenant)}/oauth2/v2.0/token"


def _claims(jwt: str) -> dict:
    try:
        part = jwt.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def expires_at(access_token: str) -> int:
    return int(_claims(access_token).get("exp") or 0)


def _form_post(url: str, fields: dict[str, str], timeout: float = 30.0) -> tuple[int, dict]:
    """POST form-urlencoded; tolerate 400 (Microsoft signals device-flow state as 400+JSON)."""
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
                 "User-Agent": _UA}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read().decode("utf-8", "replace") or "null")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        try:
            return e.code, json.loads(body or "null")
        except json.JSONDecodeError:
            return e.code, {"error": "http_error", "error_description": body[:300]}


# ── per-client token store (D-37: secrets → CredVault entry, status/health → plain sidecar) ──
CRED_LABEL = "m365_oauth"                         # kept for reference; per-service via _svc()
_SECRET_KEYS = ("refresh_token", "access_token")


def _notes(service: str) -> str:
    return (f"{_svc(service)['name']} delegated OAuth tokens — managed by the sign-in flow and "
            f"the auto-renewer; rotates on every refresh. Do not edit (delete = disconnect).")


def _fp(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:7] if value else "—"


def _read_side(cfg: Config, tenant: str, service: str = "m365") -> dict:
    try:
        return json.loads(_store_path(cfg, tenant, service).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_side(cfg: Config, tenant: str, side: dict, service: str = "m365") -> None:
    p = _store_path(cfg, tenant, service)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(side).encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _migrate_legacy(cfg: Config, tenant: str, side: dict, service: str = "m365") -> bool:
    """Move inline secrets (pre-D-37 sidecar, or a locked-vault fallback write) into the
    CredVault. False when the vault isn't usable yet — the file keeps working as-is until the
    first use after an unlock."""
    try:
        get_credvault(cfg).upsert(tenant, _svc(service)["label"],
                                  {k: side.get(k) or "" for k in _SECRET_KEYS},
                                  notes=_notes(service), actor=f"system:{service} (migrated)")
    except VaultLocked:
        return False
    refresh = side.get("refresh_token") or ""
    access = side.get("access_token") or ""
    for k in _SECRET_KEYS:
        side.pop(k, None)
    side["connected"] = True
    side["refresh_fp"] = _fp(refresh)
    side.setdefault("access_expires", expires_at(access) if access else 0)
    _write_side(cfg, tenant, side, service)
    return True


def migrate_inline_secrets(cfg: Config) -> dict:
    """Sweep every client × service and move any inline-fallback secrets (written while the vault
    was locked, D-37) into the CredVault. Call this right after the vault is unlocked so a token
    connected during a locked window lands in the client's credentials file promptly, instead of
    waiting for its next use. Returns {moved, skipped}."""
    root = _clients_root(cfg)
    moved, skipped = [], []
    try:
        dirs = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return {"moved": moved, "skipped": skipped}
    for d in dirs:
        for service, s in _SERVICES.items():
            side = _read_side(cfg, d.name, service)
            if not side.get("refresh_token"):
                continue                              # already vault-held (or not connected)
            tag = f"{d.name}/{s['label']}"
            (moved if _migrate_legacy(cfg, d.name, side, service) else skipped).append(tag)
    return {"moved": moved, "skipped": skipped}


def load_tokens(cfg: Config, tenant: str, service: str = "m365") -> dict:
    """Merged view (secrets + status) for the token lifecycle. Raises VaultLocked only when the
    secrets are vault-held and the vault can't be opened. Self-heals the sidecar to disconnected
    if the owner deleted the service's entry in the credentials manager."""
    side = _read_side(cfg, tenant, service)
    if side.get("refresh_token"):
        if not _migrate_legacy(cfg, tenant, side, service):
            return dict(side)
        side = _read_side(cfg, tenant, service)
    if not side.get("connected"):
        return dict(side)
    try:
        fields = get_credvault(cfg).resolve(tenant, _svc(service)["label"])["fields"]
    except ValueError:                            # entry deleted by the owner → disconnected
        side.pop("connected", None)
        side.pop("refresh_fp", None)
        _write_side(cfg, tenant, side, service)
        return dict(side)
    return {**side, **{k: v for k, v in fields.items() if v}}


def save_tokens(cfg: Config, tenant: str, data: dict, *, actor: str = "",
                service: str = "m365") -> None:
    """Split write (D-37): the tokens go to the client's CredVault entry; everything else
    (tenant_id + health metadata) to the plain sidecar. A locked/uninitialized vault keeps the
    secrets inline in the sidecar (pre-D-37 posture) so a rotation is never lost — they migrate
    on the first unlocked use."""
    side = {k: v for k, v in data.items() if k not in _SECRET_KEYS}
    refresh = data.get("refresh_token") or ""
    access = data.get("access_token") or ""
    if refresh:
        side["connected"] = True
        side["refresh_fp"] = _fp(refresh)
        side["access_expires"] = expires_at(access) if access else 0
        try:
            get_credvault(cfg).upsert(tenant, _svc(service)["label"],
                                      {"refresh_token": refresh, "access_token": access},
                                      notes=_notes(service),
                                      actor=actor or f"system:{service}")
        except VaultLocked:
            side["refresh_token"] = refresh
            if access:
                side["access_token"] = access
    _write_side(cfg, tenant, side, service)


def clear_tokens(cfg: Config, tenant: str, service: str = "m365") -> bool:
    """Disconnect: delete the CredVault entry AND the sidecar. Raises VaultLocked when the secrets
    are vault-held but the vault can't be opened — a disconnect must really delete the token, not
    leave it recoverable."""
    side = _read_side(cfg, tenant, service)
    removed = False
    try:
        get_credvault(cfg).delete(tenant, _svc(service)["label"])
        removed = True
    except ValueError:
        pass                                      # no vault entry (legacy-only / never connected)
    except VaultLocked:
        if side.get("connected") and not side.get("refresh_token"):
            raise                                 # the secret IS in the vault — unlock to remove it
    try:
        _store_path(cfg, tenant, service).unlink()
        removed = True
    except OSError:
        pass
    return removed


def is_connected(cfg: Config, tenant: str, service: str = "m365") -> bool:
    side = _read_side(cfg, tenant, service)
    return bool(side.get("refresh_token") or side.get("connected"))


def fingerprint_for(cfg: Config, tenant: str, service: str = "m365") -> str:
    side = _read_side(cfg, tenant, service)
    rt = side.get("refresh_token") or ""          # legacy inline only
    return _fp(rt) if rt else (side.get("refresh_fp") or "—")


def list_connected(cfg: Config, service: str = "m365") -> list[str]:
    root = _clients_root(cfg)
    fname = _svc(service)["file"]
    out = []
    try:
        for d in root.iterdir():
            if (d / fname).is_file() and is_connected(cfg, d.name, service):
                out.append(d.name)
    except OSError:
        pass
    return out


# ── token lifecycle (per client) ──
def ensure_fresh(cfg: Config, tenant: str, *, force: bool = False, service: str = "m365") -> str:
    """Return a valid access token for one client+service, refreshing + persisting near expiry.
    force=True always performs a real refresh-grant (keep-alive)."""
    name = _svc(service)["name"]
    with _refresh_lock:
        try:
            toks = load_tokens(cfg, tenant, service)
        except VaultLocked:
            raise VaultLocked(
                f"{name} ('{tenant}'): the credential vault is locked — unlock it (or "
                f"enable agent auto-unlock) so the stored token can be used") from None
        access = toks.get("access_token") or ""
        if not force and access and expires_at(access) > time.time() + _SKEW_S:
            return access
        refresh = toks.get("refresh_token") or ""
        client_id = _client_id(cfg, service)
        if not refresh:
            raise MissingCredential(
                f"{name}: client '{tenant}' is not signed in — connect it on the M365 card")
        tid = toks.get("tenant_id") or _global_tenant(cfg)
        status, data = _form_post(_token_url(tid), {
            "grant_type": "refresh_token", "client_id": client_id,
            "refresh_token": refresh, "scope": _refresh_scope(cfg, tenant, service)})
        new_access = (data or {}).get("access_token") or ""
        if not new_access:
            err = (data or {}).get("error_description") or (data or {}).get("error") or "no access_token"
            side = _read_side(cfg, tenant, service)   # record the failure for the health view —
            side["last_error"] = err[:200]            # sidecar only; the refresh token is kept
            side["last_error_at"] = int(time.time())  # untouched so re-auth stays possible
            _write_side(cfg, tenant, side, service)
            raise MissingCredential(
                f"{name} ('{tenant}') token refresh failed — re-sign-in may be needed: {err[:160]}")
        toks.update({
            "tenant_id": _claims(new_access).get("tid") or tid,
            "refresh_token": (data or {}).get("refresh_token") or refresh,
            "access_token": new_access,
            "last_refresh": int(time.time())})
        toks.pop("last_error", None)
        toks.pop("last_error_at", None)
        save_tokens(cfg, tenant, toks, service=service)
        return new_access


def token_source(cfg: Config, tenant: str, service: str = "m365") -> Callable[[], str]:
    return lambda: ensure_fresh(cfg, tenant, service=service)


# ── Security & Compliance tokens (D-58) ──
# New-ProtectionAlert etc. live on ps.compliance.protection.outlook.com — a different token
# AUDIENCE but the SAME first-party app as Exchange. So the client's existing EXO refresh
# token is redeemed for the compliance scope (exactly what Connect-IPPSSession does after
# one sign-in) — no third per-client sign-in. Cached in-process per tenant.
IPPS_SCOPE = "https://ps.compliance.protection.outlook.com/.default"
_ipps_cache: dict[str, tuple[str, float]] = {}


def compliance_token(cfg: Config, tenant: str) -> str:
    with _refresh_lock:
        tok, exp = _ipps_cache.get(tenant, ("", 0.0))
        if tok and exp > time.time() + _SKEW_S:
            return tok
        toks = load_tokens(cfg, tenant, "exo")
        refresh = toks.get("refresh_token") or ""
        if not refresh:
            raise MissingCredential(
                f"Exchange Online: client '{tenant}' is not signed in — connect Exchange on "
                f"the M365 card (the compliance alert tools ride that sign-in)")
        tid = toks.get("tenant_id") or _global_tenant(cfg)
        status, data = _form_post(_token_url(tid), {
            "grant_type": "refresh_token", "client_id": _client_id(cfg, "exo"),
            "refresh_token": refresh,
            "scope": f"{IPPS_SCOPE} offline_access openid profile"})
        access = (data or {}).get("access_token") or ""
        if not access:
            err = (data or {}).get("error_description") or (data or {}).get("error") or "no token"
            raise MissingCredential(
                f"Security & Compliance token for '{tenant}' failed — {str(err)[:160]}")
        new_rt = (data or {}).get("refresh_token") or ""
        if new_rt and new_rt != refresh:               # persist rotation, keep the chain healthy
            toks["refresh_token"] = new_rt
            save_tokens(cfg, tenant, toks, service="exo")
        _ipps_cache[tenant] = (access, float(expires_at(access)))
        return access


def compliance_token_source(cfg: Config, tenant: str) -> Callable[[], str]:
    return lambda: compliance_token(cfg, tenant)


# ── device-code sign-in (per client) ──
def _gc_flows() -> None:
    now = time.time()
    with _flows_lock:
        for fid in [f for f, v in _flows.items() if v["expires"] < now]:
            _flows.pop(fid, None)


def start_device_auth(cfg: Config, mspai_tenant: str, service: str = "m365") -> dict:
    """Begin a device sign-in FOR ONE managed client + service. Returns user_code + URI + flow_id."""
    if (mspai_tenant or "").strip() in ("", "*"):
        raise MissingCredential(f"pick a specific client to sign in to {_svc(service)['name']}")
    client_id = _client_id(cfg, service)        # built-in Microsoft app unless overridden
    auth_tenant, scopes = _global_tenant(cfg), _scopes(cfg, service)
    spo_hosts: dict[str, str] = {}
    if service == "spo":                        # per-tenant SharePoint resource (D-89)
        spo_hosts = sharepoint_hosts(cfg, mspai_tenant)   # raises if Graph isn't connected
        scopes = _spo_scope(spo_hosts["admin_host"])
    status, data = _form_post(_devicecode_url(auth_tenant), {"client_id": client_id, "scope": scopes})
    data = data or {}
    if not data.get("device_code") or not data.get("user_code"):
        err = data.get("error_description") or data.get("error") or "unexpected response"
        raise MissingCredential(f"Microsoft device sign-in could not start: {err[:200]}")
    _gc_flows()
    flow_id = _secrets.token_urlsafe(18)
    try:
        interval = max(3, int(data.get("interval", 5)))
    except (TypeError, ValueError):
        interval = 5
    try:
        expires_in = int(data.get("expires_in", 900))
    except (TypeError, ValueError):
        expires_in = 900
    with _flows_lock:
        _flows[flow_id] = {"device_code": data["device_code"], "auth_tenant": auth_tenant,
                           "client_id": client_id, "mspai_tenant": mspai_tenant, "service": service,
                           "expires": time.time() + expires_in, **spo_hosts}
    return {"flow_id": flow_id, "user_code": data["user_code"],
            "verification_uri": data.get("verification_uri") or "https://microsoft.com/devicelogin",
            # RFC 8628 optional: a URL with the code pre-filled (one click, no typing) when MS sends it
            "verification_uri_complete": data.get("verification_uri_complete") or "",
            "interval": interval, "expires_in": expires_in, "tenant": mspai_tenant,
            "service": service, "message": data.get("message") or ""}


def poll_device_auth(flow_id: str, cfg: Config) -> tuple[str, Optional[str]]:
    """One poll. ('pending'|'connected'|'error', message?). On success the tokens are saved under
    the flow's managed client."""
    with _flows_lock:
        flow = _flows.get(flow_id)
    if not flow:
        return "error", "this sign-in expired — start again"
    if flow["expires"] < time.time():
        with _flows_lock:
            _flows.pop(flow_id, None)
        return "error", "the code expired — start again"
    status, data = _form_post(_token_url(flow["auth_tenant"]), {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": flow["client_id"], "device_code": flow["device_code"]})
    data = data or {}
    err = data.get("error")
    if err in ("authorization_pending", "slow_down"):
        return "pending", None
    if err:
        if err != "expired_token":
            with _flows_lock:
                _flows.pop(flow_id, None)
        return "error", (data.get("error_description") or err)[:200]
    access = data.get("access_token") or ""
    refresh = data.get("refresh_token") or ""
    if not (access and refresh):
        return "error", "sign-in returned no tokens (is offline_access in the scopes?)"
    now = int(time.time())
    service = flow.get("service") or "m365"
    # SharePoint's per-tenant hosts (discovered at start) ride into the sidecar so token refresh
    # can rebuild the SharePoint scope and the OneDrive tool knows the -my host (D-89).
    hosts = {k: flow[k] for k in ("root_host", "admin_host", "my_host") if flow.get(k)}
    save_tokens(cfg, flow["mspai_tenant"], {
        "tenant_id": _claims(access).get("tid") or flow["auth_tenant"],
        "refresh_token": refresh, "access_token": access,
        "obtained": now, "last_refresh": now, **hosts,
        "upn": _claims(access).get("upn") or _claims(access).get("unique_name") or ""},
        actor=f"system:{service} (sign-in)", service=service)
    with _flows_lock:
        _flows.pop(flow_id, None)
    return "connected", flow["mspai_tenant"]


# ── token health + auto-renew (keep-alive) ──
# Microsoft delegated refresh tokens stay valid ~90 days from last use; the renewer below uses each
# one periodically so it never goes idle-stale. A token only truly dies on password/MFA/Conditional-
# Access change or admin revocation — that surfaces as last_error and a "re-sign-in needed" state.
REFRESH_TTL_DAYS = 90


def health(cfg: Config, tenant: str, service: str = "m365") -> dict:
    """Sidecar-only (no vault decrypt) — the card and the renewer's worklist stay readable even
    while the vault is locked."""
    side = _read_side(cfg, tenant, service)
    if not (side.get("refresh_token") or side.get("connected")):
        return {"connected": False}
    access = side.get("access_token") or ""       # legacy inline only
    last = int(side.get("last_refresh") or side.get("obtained") or 0)
    return {"connected": True,
            "obtained": side.get("obtained"),
            "last_refresh": last,
            "access_expires": int(side.get("access_expires") or (expires_at(access) if access else 0)),
            "refresh_valid_until": (last + REFRESH_TTL_DAYS * 86400) if last else 0,
            "last_error": side.get("last_error"),
            "healthy": not side.get("last_error")}


def admin_upn(cfg: Config, tenant: str, service: str = "exo") -> str:
    """The signing admin's UPN (captured at sign-in; non-secret) — EXO routing needs it."""
    return str(_read_side(cfg, tenant, service).get("upn") or "")


def renew(cfg: Config, tenant: str, service: str = "m365") -> bool:
    """Force a refresh now (keep-alive) — resets the 90-day idle clock. Returns ok. Raises
    VaultLocked when the token is vault-held and the vault is locked — that is NOT a token
    failure, so it must not surface as 're-sign-in needed'."""
    if not is_connected(cfg, tenant, service):
        return False
    try:
        ensure_fresh(cfg, tenant, force=True, service=service)
        return True
    except VaultLocked:
        raise
    except Exception:
        return False                            # ensure_fresh recorded last_error


def renew_all(cfg: Config, service: Optional[str] = None) -> dict:
    """Keep-alive across clients; service=None covers every service (the renewer daemon)."""
    ok, failed, locked = [], [], []
    for svc in ([service] if service else list(_SERVICES)):
        for t in list_connected(cfg, svc):
            tag = t if svc == "m365" else f"{t} ({_svc(svc)['name']})"
            try:
                (ok if renew(cfg, t, svc) else failed).append(tag)
            except VaultLocked:
                locked.append(tag)
    return {"ok": ok, "failed": failed, "locked": locked}
