"""TOTP (RFC 6238) — stdlib authenticator-app MFA. No pyotp dependency.

SHA-1 / 6-digit / 30-second step → compatible with Google Authenticator, Microsoft Authenticator,
Authy, 1Password, etc. Verification checks a ±1 step window for clock skew, constant-time.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

_STEP = 30
_DIGITS = 6


def generate_secret(length: int = 20) -> str:
    """A base32 secret (no padding) — 20 bytes = 160-bit, the RFC-recommended size."""
    return base64.b32encode(secrets.token_bytes(length)).decode("ascii").rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    pad = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + pad, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** _DIGITS)
    return str(code).zfill(_DIGITS)


def now_code(secret: str, t: float | None = None) -> str:
    return _hotp(secret, int((t if t is not None else time.time()) // _STEP))


def verify(secret: str, code: str, *, t: float | None = None, window: int = 1) -> bool:
    """True if `code` matches the current step (or ±`window` steps). Constant-time per candidate."""
    cleaned = (code or "").strip().replace(" ", "").replace("-", "")
    if not secret or not cleaned.isdigit() or len(cleaned) != _DIGITS:
        return False
    counter = int((t if t is not None else time.time()) // _STEP)
    ok = False
    for w in range(-window, window + 1):
        # don't short-circuit — keep the comparison count constant
        ok = hmac.compare_digest(_hotp(secret, counter + w), cleaned) or ok
    return ok


def provisioning_uri(secret: str, account: str, issuer: str = "MSP AI") -> str:
    """otpauth:// URI to render as a QR (or paste the secret manually)."""
    label = quote(f"{issuer}:{account}", safe="")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe='')}"
            f"&algorithm=SHA1&digits={_DIGITS}&period={_STEP}")
