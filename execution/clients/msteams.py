"""Microsoft Teams Bot Framework client (D-29) — token cache, JWT verify, send, probe.

Ported from the Hermes teams adapter's security model:
  - outbound service URL must be a known Bot Framework host (blocks SSRF / token
    exfiltration via a tampered serviceUrl or env var)
  - conversation ids are validated against the documented character set (no path escape)
  - inbound webhook JWTs are verified against login.botframework.com's signing keys
    (issuer https://api.botframework.com, audience = our TEAMS_CLIENT_ID)

Built via credentials.require() like every client (I-2). Replies/sends are plain
Bot Framework REST — no SDK.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from ._http import HttpError, http_json

DEFAULT_SERVICE_URL = "https://smba.trafficmanager.net/teams/"
ALLOWED_SERVICE_HOSTS = frozenset({
    "smba.trafficmanager.net",
    "smba.infra.gov.teams.microsoft.us",
})
_CONV_ID_RE = re.compile(r"^[A-Za-z0-9:@\-_.=]+$")
_TENANT_RE = re.compile(r"^[A-Za-z0-9\-.]+$")

_OPENID_CONFIG_URL = "https://login.botframework.com/v1/.well-known/openidconfiguration"
_EXPECTED_ISSUER = "https://api.botframework.com"


def validate_service_url(raw: str) -> Optional[str]:
    """Normalized https service URL on an allowlisted Bot Framework host, else None."""
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_SERVICE_HOSTS:
        return None
    return raw if raw.endswith("/") else raw + "/"


class TeamsAuthError(Exception):
    """Inbound webhook JWT failed verification — the request must be rejected."""


# One JWKS client per process — Bot Framework signing keys are global, and PyJWKClient
# caches fetched keys internally.
_jwks_lock = threading.Lock()
_jwks_client = None


def verify_bot_jwt(auth_header: str, client_id: str) -> dict[str, Any]:
    """Verify an incoming Bot Framework JWT (Authorization: Bearer ...). Returns the
    decoded claims or raises TeamsAuthError. Fail-closed on every error path."""
    if not client_id:
        raise TeamsAuthError("TEAMS_CLIENT_ID not configured")
    token = (auth_header or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise TeamsAuthError("missing bearer token")
    try:
        import jwt as pyjwt
        from jwt import PyJWKClient
    except ImportError as e:                       # pragma: no cover
        raise TeamsAuthError(f"PyJWT unavailable — cannot verify webhook ({e})")
    global _jwks_client
    try:
        with _jwks_lock:
            if _jwks_client is None:
                _s, conf = http_json("GET", _OPENID_CONFIG_URL)
                jwks_uri = (conf or {}).get("jwks_uri") or ""
                if not jwks_uri.startswith("https://"):
                    raise TeamsAuthError("bad jwks_uri from Bot Framework metadata")
                _jwks_client = PyJWKClient(jwks_uri, cache_keys=True)
            key = _jwks_client.get_signing_key_from_jwt(token).key
        claims = pyjwt.decode(
            token, key, algorithms=["RS256"], audience=client_id,
            issuer=_EXPECTED_ISSUER,
            options={"require": ["exp", "iss", "aud"]},
        )
        return claims
    except TeamsAuthError:
        raise
    except Exception as e:
        raise TeamsAuthError(f"JWT verification failed: {type(e).__name__}: {e}")


class TeamsClient:
    """Outbound Bot Framework REST: app token + conversation activities.

    Authenticates with EITHER the app's client secret OR the locally generated app
    certificate (core/teams_cert.py) via a signed client assertion. When both exist,
    the certificate wins (no shared secret on the wire)."""

    def __init__(self, client_id: str, client_secret: str, tenant_id: str,
                 *, service_url: str = "", transport: Callable = http_json) -> None:
        if not (client_id and tenant_id):
            raise ValueError("TEAMS_CLIENT_ID and TEAMS_TENANT_ID are required")
        if not _TENANT_RE.match(tenant_id):
            raise ValueError("TEAMS_TENANT_ID contains unexpected characters")
        self.client_id = client_id
        self.client_secret = client_secret or ""
        self.tenant_id = tenant_id
        if not self.client_secret and not self._cert_exists():
            raise ValueError("set a TEAMS_CLIENT_SECRET or generate an app certificate "
                             "on the Microsoft Teams integration card")
        self.default_service_url = validate_service_url(service_url) or DEFAULT_SERVICE_URL
        self._t = transport
        self._token: Optional[str] = None
        self._token_exp = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _cert_exists() -> bool:
        try:
            from ..core import teams_cert
            return teams_cert.exists()
        except Exception:
            return False

    @property
    def auth_method(self) -> str:
        return "certificate" if self._cert_exists() else "client_secret"

    def _token_request_fields(self) -> dict[str, str]:
        """Client-credentials body — certificate assertion when a cert exists, else secret."""
        fields = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "scope": "https://api.botframework.com/.default",
        }
        if self._cert_exists():
            from ..core import teams_cert
            fields["client_assertion_type"] = \
                "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            fields["client_assertion"] = teams_cert.client_assertion(
                self.client_id, self.tenant_id)
        else:
            fields["client_secret"] = self.client_secret
        return fields

    # ── app token (client credentials; cached until ~expiry) ──
    def _get_token(self) -> str:
        with self._lock:
            if self._token and time.monotonic() < self._token_exp - 60:
                return self._token
            import urllib.parse
            import urllib.request
            body = urllib.parse.urlencode(self._token_request_fields()).encode()
            url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            import json as _json
            import urllib.error
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    payload = _json.loads(resp.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace") if e.fp else ""
                raise HttpError(e.code, raw) from e
            token = payload.get("access_token")
            if not token:
                raise HttpError(502, "token response missing access_token")
            self._token = token
            self._token_exp = time.monotonic() + float(payload.get("expires_in") or 3600)
            return token

    # ── activities ──
    def _activities_url(self, conversation_id: str, service_url: str = "",
                        reply_to: str = "") -> str:
        svc = validate_service_url(service_url) or self.default_service_url
        if svc is None:
            raise ValueError("service URL host is not on the Bot Framework allowlist")
        if not conversation_id or not _CONV_ID_RE.match(conversation_id):
            raise ValueError("conversation id contains characters outside the Bot Framework set")
        base = f"{svc}v3/conversations/{conversation_id}/activities"
        if reply_to:
            if not _CONV_ID_RE.match(reply_to):
                raise ValueError("reply-to activity id contains unexpected characters")
            base += f"/{reply_to}"
        return base

    def send_text(self, conversation_id: str, text: str, *, service_url: str = "",
                  reply_to: str = "") -> dict[str, Any]:
        token = self._get_token()
        activity = {"type": "message", "text": text or "", "textFormat": "markdown"}
        if reply_to:
            activity["replyToId"] = reply_to
        url = self._activities_url(conversation_id, service_url, reply_to)
        _s, data = self._t("POST", url, headers={"Authorization": f"Bearer {token}"},
                           json_body=activity)
        return {"ok": True, "message_id": (data or {}).get("id") if isinstance(data, dict) else None}

    def send_card(self, conversation_id: str, card: dict, *, text: str = "",
                  service_url: str = "", reply_to: str = "") -> dict[str, Any]:
        """Post an Adaptive Card (e.g. the approve/repeat/deny buttons). `card` is the card content;
        Action.Submit taps come back as a normal message activity with `value` = the action's data."""
        token = self._get_token()
        activity: dict[str, Any] = {
            "type": "message",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive",
                             "content": card}],
        }
        if text:
            activity["text"] = text
            activity["textFormat"] = "markdown"
        if reply_to:
            activity["replyToId"] = reply_to
        url = self._activities_url(conversation_id, service_url, reply_to)
        _s, data = self._t("POST", url, headers={"Authorization": f"Bearer {token}"},
                           json_body=activity)
        return {"ok": True, "message_id": (data or {}).get("id") if isinstance(data, dict) else None}

    def send_typing(self, conversation_id: str, *, service_url: str = "") -> None:
        try:
            token = self._get_token()
            url = self._activities_url(conversation_id, service_url)
            self._t("POST", url, headers={"Authorization": f"Bearer {token}"},
                    json_body={"type": "typing"})
        except Exception:
            pass                                   # cosmetic — never let typing break a turn

    # ── probe: prove the app registration works (token grant), no message sent ──
    def probe(self) -> dict[str, Any]:
        try:
            self._get_token()
            return {"ok": True, "detail": f"Bot Framework token grant ok "
                                          f"(auth: {self.auth_method})"}
        except HttpError as e:
            hint = ("check client id/tenant id and that the certificate is uploaded in Entra"
                    if self.auth_method == "certificate"
                    else "check client id/secret/tenant id") if e.status in (400, 401) else ""
            return {"ok": False, "detail": f"token grant failed: HTTP {e.status} {hint}".strip()}
        except (OSError, ValueError) as e:
            return {"ok": False, "detail": str(e)}


# ── allowlist (default deny; entries 'aad-object-id|Display Name|mspai-username') ──
# The optional third part links the Teams user to a dashboard account (D-31), so the agent
# knows who it is talking to (their email + saved profile) when messaged from Teams.
def parse_allowlist(raw: str) -> list[dict[str, str]]:
    out = []
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("|")
        out.append({"id": parts[0].strip(),
                    "name": parts[1].strip() if len(parts) > 1 else "",
                    "user": parts[2].strip() if len(parts) > 2 else ""})
    return out


def user_allowed(env: dict[str, str], aad_object_id: str) -> tuple[bool, str]:
    """Default-deny allowlist check, mirroring Hermes: TEAMS_ALLOWED_USERS holds the ids;
    TEAMS_ALLOW_ALL_USERS=true is the explicit opt-out. No config at all → deny everyone."""
    if str(env.get("TEAMS_ALLOW_ALL_USERS") or "").strip().lower() in ("1", "true", "yes"):
        return True, "allow-all enabled"
    entries = parse_allowlist(env.get("TEAMS_ALLOWED_USERS") or "")
    if not entries:
        return False, ("no users are allowed yet — add your AAD object id to the allowlist "
                       "on the Microsoft Teams integration card")
    ids = {e["id"] for e in entries if e["id"]}
    if "*" in ids or (aad_object_id and aad_object_id in ids):
        return True, "ok"
    return False, "user is not on the Teams allowlist"
