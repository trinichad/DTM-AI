"""Google Workspace admin OAuth — PER CLIENT, authorization-code flow (D-118).

MSP model: ONE OAuth app (the owner's Google Cloud project — GWS_CLIENT_ID/SECRET/REDIRECT are
global config), but each managed client's super-admin signs in SEPARATELY and consents. That
client's refresh token is stored UNDER THAT CLIENT, so one client's Google access never bleeds
into another's — the same isolation posture as M365 (m365_auth).

Why authorization-code, not device-code: Google's device ("limited-input") flow does NOT permit
Admin SDK / Directory scopes. So per-client OAuth here is redirect-based — the admin visits a Google
consent URL and Google redirects to our callback with a `code` we exchange for tokens. `start_auth`
builds the URL + a short-lived `state`; `complete_auth` (called by the web callback) redeems the code.

Per-client token store (D-37 split): SECRETS (refresh_token + cached access_token) live in the
client's encrypted CredVault entry 'gws_oauth'; non-secret status/health stays in the plain sidecar
<vault>/clients/<tenant>/gws.json (0600) — {tenant_id?, connected, refresh_fp, access_expires,
obtained, last_refresh, admin_email, domain, last_error?}. Locked-vault fallback keeps secrets inline
and migrates on first unlocked use (same as M365).

Google specifics vs M365: access tokens are OPAQUE (not JWTs), so expiry is tracked as
`access_expires = now + expires_in` rather than read from the token; the refresh grant returns a new
access token but usually NO new refresh token (we keep the existing one); `access_type=offline` +
`prompt=consent` guarantee a refresh token is issued.
SOP: architecture/google-workspace.md.
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

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

CLIENT_ID_KEY = "GWS_CLIENT_ID"
CLIENT_SECRET_KEY = "GWS_CLIENT_SECRET"
REDIRECT_KEY = "GWS_REDIRECT_URI"
SCOPES_KEY = "GWS_SCOPES"

# Read-first default scopes (Phase 1). Write/other scopes are added as their tool phases land; a
# scope change requires the client to re-consent (same rule as M365's re-sign-in).
DEFAULT_SCOPES = (
    "openid email "
    "https://www.googleapis.com/auth/admin.directory.user.readonly "
    "https://www.googleapis.com/auth/admin.directory.group.readonly"
)

CRED_LABEL = "gws_oauth"
_SECRET_KEYS = ("refresh_token", "access_token")
_SKEW_S = 300
_UA = "MSP-AI-GWS/1.0"

_refresh_lock = threading.Lock()
_flows: dict[str, dict] = {}
_flows_lock = threading.Lock()


# ── config accessors ──
def _client_id(cfg: Config) -> str:
    return (cfg.get(CLIENT_ID_KEY) or "").strip()


def _client_secret(cfg: Config) -> str:
    return (cfg.get(CLIENT_SECRET_KEY) or "").strip()


def _redirect_uri(cfg: Config) -> str:
    return (cfg.get(REDIRECT_KEY) or "").strip()


def _scopes(cfg: Config) -> str:
    return (cfg.get(SCOPES_KEY) or DEFAULT_SCOPES).strip() or DEFAULT_SCOPES


def is_configured(cfg: Config) -> bool:
    """The owner's OAuth app is set up enough to start a per-client sign-in."""
    return bool(_client_id(cfg) and _client_secret(cfg) and _redirect_uri(cfg))


# ── paths / sidecar ──
def _safe_tenant(tenant: str) -> str:
    cleaned = _SAFE.sub("_", tenant or "").strip("._")
    return cleaned or "_unknown"


def _clients_root(cfg: Config) -> Path:
    return Path(cfg.get("MSPAI_VAULT_PATH") or (_PROJECT_ROOT / "vault")) / "clients"


def _store_path(cfg: Config, tenant: str) -> Path:
    return _clients_root(cfg) / _safe_tenant(tenant) / "gws.json"


def _fp(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:7] if value else "—"


def _notes() -> str:
    return ("Google Workspace delegated OAuth tokens — managed by the sign-in flow and the "
            "auto-renewer; may rotate on refresh. Do not edit (delete = disconnect).")


def _read_side(cfg: Config, tenant: str) -> dict:
    try:
        return json.loads(_store_path(cfg, tenant).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_side(cfg: Config, tenant: str, side: dict) -> None:
    p = _store_path(cfg, tenant)
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


def _claims(jwt: str) -> dict:
    """Decode a JWT payload (the exchange's id_token) — best-effort, no signature check (the token
    came straight from Google's token endpoint over TLS)."""
    try:
        part = jwt.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def _form_post(url: str, fields: dict[str, str], timeout: float = 30.0) -> tuple[int, dict]:
    """POST form-urlencoded to Google's token endpoint; tolerate 4xx (errors come back as JSON)."""
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


# ── per-client token store (D-37: secrets → CredVault, status/health → plain sidecar) ──
def _migrate_legacy(cfg: Config, tenant: str, side: dict) -> bool:
    """Move inline secrets (locked-vault fallback) into the CredVault. False when the vault isn't
    usable yet — the sidecar keeps working as-is until the first use after an unlock."""
    try:
        get_credvault(cfg).upsert(tenant, CRED_LABEL,
                                  {k: side.get(k) or "" for k in _SECRET_KEYS},
                                  notes=_notes(), actor="system:gws (migrated)")
    except VaultLocked:
        return False
    refresh = side.get("refresh_token") or ""
    for k in _SECRET_KEYS:
        side.pop(k, None)
    side["connected"] = True
    side["refresh_fp"] = _fp(refresh)
    _write_side(cfg, tenant, side)
    return True


def load_tokens(cfg: Config, tenant: str) -> dict:
    """Merged view (secrets + status) for the token lifecycle. Raises VaultLocked only when secrets
    are vault-held and the vault can't be opened. Self-heals to disconnected if the owner deleted
    the entry in the credentials manager."""
    side = _read_side(cfg, tenant)
    if side.get("refresh_token"):
        if not _migrate_legacy(cfg, tenant, side):
            return dict(side)
        side = _read_side(cfg, tenant)
    if not side.get("connected"):
        return dict(side)
    try:
        fields = get_credvault(cfg).resolve(tenant, CRED_LABEL)["fields"]
    except ValueError:                            # entry deleted by the owner → disconnected
        side.pop("connected", None)
        side.pop("refresh_fp", None)
        _write_side(cfg, tenant, side)
        return dict(side)
    return {**side, **{k: v for k, v in fields.items() if v}}


def save_tokens(cfg: Config, tenant: str, data: dict, *, actor: str = "") -> None:
    """Split write (D-37): tokens → the client's CredVault entry; everything else (tenant_id,
    access_expires, health metadata) → the plain sidecar. A locked vault keeps the secrets inline
    so a rotation is never lost; they migrate on the first unlocked use."""
    side = {k: v for k, v in data.items() if k not in _SECRET_KEYS}
    refresh = data.get("refresh_token") or ""
    access = data.get("access_token") or ""
    if refresh:
        side["connected"] = True
        side["refresh_fp"] = _fp(refresh)
        try:
            get_credvault(cfg).upsert(tenant, CRED_LABEL,
                                      {"refresh_token": refresh, "access_token": access},
                                      notes=_notes(), actor=actor or "system:gws")
        except VaultLocked:
            side["refresh_token"] = refresh
            if access:
                side["access_token"] = access
    _write_side(cfg, tenant, side)


def clear_tokens(cfg: Config, tenant: str) -> bool:
    """Disconnect: delete the CredVault entry AND the sidecar. Raises VaultLocked when secrets are
    vault-held but the vault can't be opened — a disconnect must really delete the token."""
    side = _read_side(cfg, tenant)
    removed = False
    try:
        get_credvault(cfg).delete(tenant, CRED_LABEL)
        removed = True
    except ValueError:
        pass
    except VaultLocked:
        if side.get("connected") and not side.get("refresh_token"):
            raise
    try:
        _store_path(cfg, tenant).unlink()
        removed = True
    except OSError:
        pass
    return removed


def is_connected(cfg: Config, tenant: str) -> bool:
    side = _read_side(cfg, tenant)
    return bool(side.get("refresh_token") or side.get("connected"))


def fingerprint_for(cfg: Config, tenant: str) -> str:
    side = _read_side(cfg, tenant)
    rt = side.get("refresh_token") or ""          # legacy inline only
    return _fp(rt) if rt else (side.get("refresh_fp") or "—")


def list_connected(cfg: Config) -> list[str]:
    root = _clients_root(cfg)
    out = []
    try:
        for d in root.iterdir():
            if (d / "gws.json").is_file() and is_connected(cfg, d.name):
                out.append(d.name)
    except OSError:
        pass
    return out


def admin_email(cfg: Config, tenant: str) -> str:
    return str(_read_side(cfg, tenant).get("admin_email") or "")


def domain(cfg: Config, tenant: str) -> str:
    return str(_read_side(cfg, tenant).get("domain") or "")


# ── token lifecycle (per client) ──
def ensure_fresh(cfg: Config, tenant: str, *, force: bool = False,
                 transport: Callable = _form_post) -> str:
    """Return a valid access token for one client, refreshing + persisting near expiry.
    force=True always performs a real refresh-grant (keep-alive). `transport` is injectable
    (defaults to the real token endpoint) so the refresh path is unit-tested with no network."""
    with _refresh_lock:
        try:
            toks = load_tokens(cfg, tenant)
        except VaultLocked:
            raise VaultLocked(
                f"Google Workspace ('{tenant}'): the credential vault is locked — unlock it (or "
                f"enable agent auto-unlock) so the stored token can be used") from None
        access = toks.get("access_token") or ""
        exp = int(toks.get("access_expires") or 0)
        if not force and access and exp > time.time() + _SKEW_S:
            return access
        refresh = toks.get("refresh_token") or ""
        if not refresh:
            raise MissingCredential(
                f"Google Workspace: client '{tenant}' is not signed in — connect it on the "
                f"Google Workspace card")
        cid, secret = _client_id(cfg), _client_secret(cfg)
        if not (cid and secret):
            raise MissingCredential(
                "Google Workspace OAuth app is not configured — set GWS_CLIENT_ID / "
                "GWS_CLIENT_SECRET on the Google Workspace card")
        status, data = transport(TOKEN_URL, {
            "grant_type": "refresh_token", "client_id": cid, "client_secret": secret,
            "refresh_token": refresh})
        new_access = (data or {}).get("access_token") or ""
        if not new_access:
            err = (data or {}).get("error_description") or (data or {}).get("error") or "no access_token"
            side = _read_side(cfg, tenant)
            side["last_error"] = str(err)[:200]
            side["last_error_at"] = int(time.time())
            _write_side(cfg, tenant, side)
            raise MissingCredential(
                f"Google Workspace ('{tenant}') token refresh failed — re-consent may be needed: "
                f"{str(err)[:160]}")
        try:
            expires_in = int((data or {}).get("expires_in") or 3600)
        except (TypeError, ValueError):
            expires_in = 3600
        toks.update({
            "refresh_token": (data or {}).get("refresh_token") or refresh,  # Google usually reuses
            "access_token": new_access,
            "access_expires": int(time.time()) + expires_in,
            "last_refresh": int(time.time())})
        toks.pop("last_error", None)
        toks.pop("last_error_at", None)
        save_tokens(cfg, tenant, toks)
        return new_access


def token_source(cfg: Config, tenant: str) -> Callable[[], str]:
    return lambda: ensure_fresh(cfg, tenant)


# ── authorization-code sign-in (per client) ──
def _gc_flows() -> None:
    now = time.time()
    with _flows_lock:
        for st in [s for s, v in _flows.items() if v["expires"] < now]:
            _flows.pop(st, None)


def start_auth(cfg: Config, mspai_tenant: str, *, login_hint: str = "",
               hosted_domain: str = "") -> dict:
    """Begin a per-client Google sign-in. Returns {auth_url, state, expires_in} — the client's
    super-admin opens auth_url, consents, and Google redirects to GWS_REDIRECT_URI with ?code&state,
    which the web callback hands to complete_auth()."""
    if (mspai_tenant or "").strip() in ("", "*"):
        raise MissingCredential("pick a specific client to sign in to Google Workspace")
    if not is_configured(cfg):
        raise MissingCredential(
            "Google Workspace OAuth app is not configured — set GWS_CLIENT_ID, GWS_CLIENT_SECRET "
            "and GWS_REDIRECT_URI on the Google Workspace card first")
    _gc_flows()
    state = _secrets.token_urlsafe(24)
    params = {
        "client_id": _client_id(cfg),
        "redirect_uri": _redirect_uri(cfg),
        "response_type": "code",
        "scope": _scopes(cfg),
        "access_type": "offline",       # ask for a refresh token
        "prompt": "consent",            # force it every time (Google only re-issues rt on consent)
        "include_granted_scopes": "true",
        "state": state,
    }
    if login_hint.strip():
        params["login_hint"] = login_hint.strip()
    if hosted_domain.strip():
        params["hd"] = hosted_domain.strip()
    with _flows_lock:
        _flows[state] = {"tenant": mspai_tenant, "expires": time.time() + 900}
    return {"auth_url": f"{AUTH_URL}?{urllib.parse.urlencode(params)}", "state": state,
            "expires_in": 900, "tenant": mspai_tenant}


def complete_auth(cfg: Config, state: str, code: str,
                  *, transport: Callable = _form_post) -> tuple[str, Optional[str]]:
    """Redeem the redirect's authorization code. ('connected', tenant) on success (tokens saved
    under the managed client), else ('error', message). Called by the web OAuth callback.
    `transport` is injectable (defaults to the real token endpoint) for no-network tests."""
    with _flows_lock:
        flow = _flows.get(state)
    if not flow:
        return "error", "this sign-in link expired or is invalid — start again"
    if flow["expires"] < time.time():
        with _flows_lock:
            _flows.pop(state, None)
        return "error", "the sign-in expired — start again"
    if not (code or "").strip():
        return "error", "Google did not return an authorization code (consent may have been denied)"
    status, data = transport(TOKEN_URL, {
        "grant_type": "authorization_code", "code": code.strip(),
        "client_id": _client_id(cfg), "client_secret": _client_secret(cfg),
        "redirect_uri": _redirect_uri(cfg)})
    data = data or {}
    access = data.get("access_token") or ""
    refresh = data.get("refresh_token") or ""
    if not access:
        err = data.get("error_description") or data.get("error") or "token exchange failed"
        return "error", str(err)[:200]
    if not refresh:
        return "error", ("Google returned no refresh token — the app must request access_type="
                         "offline with prompt=consent (and the user must grant offline access)")
    claims = _claims(data.get("id_token") or "")
    try:
        expires_in = int(data.get("expires_in") or 3600)
    except (TypeError, ValueError):
        expires_in = 3600
    now = int(time.time())
    tenant = flow["tenant"]
    save_tokens(cfg, tenant, {
        "refresh_token": refresh, "access_token": access,
        "access_expires": now + expires_in,
        "obtained": now, "last_refresh": now,
        "admin_email": claims.get("email") or "",
        "domain": claims.get("hd") or "",
        "tenant_id": claims.get("hd") or ""},
        actor="system:gws (sign-in)")
    with _flows_lock:
        _flows.pop(state, None)
    return "connected", tenant


# ── token health + keep-alive ──
def health(cfg: Config, tenant: str) -> dict:
    """Sidecar-only (no vault decrypt) so the card stays readable while the vault is locked."""
    side = _read_side(cfg, tenant)
    if not (side.get("refresh_token") or side.get("connected")):
        return {"connected": False}
    return {"connected": True,
            "obtained": side.get("obtained"),
            "last_refresh": int(side.get("last_refresh") or side.get("obtained") or 0),
            "access_expires": int(side.get("access_expires") or 0),
            "admin_email": side.get("admin_email") or "",
            "domain": side.get("domain") or "",
            "last_error": side.get("last_error"),
            "healthy": not side.get("last_error")}


def renew(cfg: Config, tenant: str) -> bool:
    """Force a refresh now (keep-alive). Returns ok. Raises VaultLocked when the token is vault-held
    and the vault is locked — that is NOT a token failure."""
    if not is_connected(cfg, tenant):
        return False
    try:
        ensure_fresh(cfg, tenant, force=True)
        return True
    except VaultLocked:
        raise
    except Exception:
        return False


def renew_all(cfg: Config) -> dict:
    """Keep-alive across every signed-in client."""
    ok, failed, locked = [], [], []
    for t in list_connected(cfg):
        try:
            (ok if renew(cfg, t) else failed).append(t)
        except VaultLocked:
            locked.append(t)
    return {"ok": ok, "failed": failed, "locked": locked}
