"""Codex OAuth token lifecycle (D-26) — OpenAI on the owner's ChatGPT plan, no API key.

The durable credential is OPENAI_CODEX_REFRESH_TOKEN; the short-lived access token (a JWT,
~10-day exp) is cached in the SecretStore and re-minted here when near expiry. Both keys
MUST live in the SecretStore (secrets.local) — process env / .env shadow the store, so a
rotated token could never be persisted and auth would die when the shadowed copy expires.

Fail-closed (Rule #8): no refresh token → MissingCredential; refresh HTTP failure → raise.
SOP: architecture/openai-codex.md.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable, Optional

from .config import Config
from .credentials import MissingCredential
from ..clients._http import HttpError, http_json

AUTH_BASE = "https://auth.openai.com"
AUTH_TOKEN_URL = f"{AUTH_BASE}/oauth/token"
DEVICE_USERCODE_URL = f"{AUTH_BASE}/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = f"{AUTH_BASE}/api/accounts/deviceauth/token"
VERIFICATION_URL = f"{AUTH_BASE}/codex/device"          # where the human enters the code
DEVICE_REDIRECT_URI = f"{AUTH_BASE}/deviceauth/callback"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"   # official Codex CLI OAuth client
ACCESS_KEY = "OPENAI_CODEX_ACCESS_TOKEN"
REFRESH_KEY = "OPENAI_CODEX_REFRESH_TOKEN"
_SKEW_S = 300                                 # refresh when < 5 min of life remains
# Cloudflare in front of auth.openai.com challenges the default Python-urllib UA; send the
# Codex client's UA on every auth call (this IS the Codex OAuth flow, same client_id).
_UA = "codex_cli_rs/0.48.0 (MSP AI)"
_HDRS = {"User-Agent": _UA}

_refresh_lock = threading.Lock()


def _claims(jwt: str) -> dict:
    """Decode a JWT payload locally (no verification — we only read exp/account hints)."""
    try:
        part = jwt.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def account_id(access_token: str) -> str:
    auth = _claims(access_token).get("https://api.openai.com/auth") or {}
    return auth.get("chatgpt_account_id", "")


def expires_at(access_token: str) -> int:
    return int(_claims(access_token).get("exp") or 0)


def _persist(cfg: Config, access: str, refresh: str) -> None:
    if cfg.secrets is None:
        return  # test/CLI config without a store — caller keeps the in-memory token
    cfg.secrets.set_many({ACCESS_KEY: access, REFRESH_KEY: refresh},
                         allowed_keys={ACCESS_KEY, REFRESH_KEY})


def ensure_fresh(cfg: Config, transport: Callable = http_json) -> tuple[str, str]:
    """Return (access_token, chatgpt_account_id), refreshing + persisting if near expiry."""
    with _refresh_lock:
        access = cfg.get(ACCESS_KEY) or ""
        if access and expires_at(access) > time.time() + _SKEW_S:
            return access, account_id(access)
        refresh = cfg.get(REFRESH_KEY) or ""
        if not refresh:
            raise MissingCredential(
                "OpenAI (ChatGPT plan): access token expired and no "
                f"{REFRESH_KEY} configured — re-link via the credential form")
        _s, data = transport("POST", AUTH_TOKEN_URL, headers=_HDRS, json_body={
            "client_id": CLIENT_ID, "grant_type": "refresh_token",
            "refresh_token": refresh, "scope": "openid profile email"}, timeout=30)
        new_access = (data or {}).get("access_token") or ""
        if not new_access:
            raise MissingCredential("OpenAI (ChatGPT plan): token refresh returned no access_token")
        _persist(cfg, new_access, (data or {}).get("refresh_token") or refresh)
        return new_access, account_id(new_access)


def token_source(cfg: Config) -> Callable[[], tuple[str, str]]:
    """A zero-arg callable the CodexProvider pulls a fresh (token, account_id) from per request."""
    return lambda: ensure_fresh(cfg)


# ── device-code sign-in (the GUI "Sign in with ChatGPT" flow) ────────────────
# OpenAI's custom (non-RFC-8628) device flow, as the Codex CLI implements it:
# usercode → human approves at VERIFICATION_URL → poll yields an authorization_code with
# SERVER-held PKCE codes → standard authorization_code exchange. SOP: openai-codex.md.

def start_device_auth(transport: Callable = http_json) -> dict:
    """Begin a device sign-in. Returns what the GUI needs to show + poll with."""
    _s, data = transport("POST", DEVICE_USERCODE_URL, headers=_HDRS,
                         json_body={"client_id": CLIENT_ID}, timeout=30)
    data = data or {}
    if not data.get("device_auth_id") or not data.get("user_code"):
        raise MissingCredential("OpenAI device auth: unexpected response (no user code)")
    try:
        interval = max(3, int(str(data.get("interval", "5")).strip()))
    except ValueError:
        interval = 5
    return {"device_auth_id": data["device_auth_id"], "user_code": data["user_code"],
            "verification_url": VERIFICATION_URL, "interval": interval,
            "expires_at": data.get("expires_at")}


def poll_device_auth(device_auth_id: str, user_code: str,
                     transport: Callable = http_json) -> tuple[str, Optional[dict]]:
    """One poll attempt. Returns ("pending", None) while the human hasn't approved yet,
    or ("ok", {authorization_code, code_verifier, ...}) once they have. Raises on real errors."""
    try:
        _s, data = transport("POST", DEVICE_TOKEN_URL, headers=_HDRS,
                             json_body={"device_auth_id": device_auth_id,
                                        "user_code": user_code}, timeout=30)
    except HttpError as e:
        if e.status in (403, 404):           # pending / not-yet-approved (matches Codex CLI)
            return "pending", None
        raise
    return "ok", data or {}


def _post_form(url: str, fields: dict[str, str], timeout: float = 30.0) -> tuple[int, dict]:
    """The token exchange is form-urlencoded (http_json only speaks JSON bodies)."""
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", **_HDRS}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.status, json.loads(resp.read().decode("utf-8", "replace") or "null")


def exchange_device_code(cfg: Config, code_resp: dict,
                         form_transport: Callable = _post_form) -> tuple[str, str]:
    """Swap the approved device authorization_code for tokens; persist; return
    (access_token, account_id) like ensure_fresh."""
    auth_code = (code_resp or {}).get("authorization_code") or ""
    verifier = (code_resp or {}).get("code_verifier") or ""
    if not auth_code or not verifier:
        raise MissingCredential("OpenAI device auth: approval response missing code/verifier")
    _s, data = form_transport(AUTH_TOKEN_URL, {
        "grant_type": "authorization_code", "code": auth_code,
        "redirect_uri": DEVICE_REDIRECT_URI, "client_id": CLIENT_ID,
        "code_verifier": verifier})
    access = (data or {}).get("access_token") or ""
    refresh = (data or {}).get("refresh_token") or ""
    if not access or not refresh:
        raise MissingCredential("OpenAI device auth: token exchange returned no tokens")
    with _refresh_lock:
        _persist(cfg, access, refresh)
    return access, account_id(access)
