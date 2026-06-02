"""Auth — stdlib PBKDF2 password hashing + HMAC-signed session tokens with TTL.

No passlib/itsdangerous dependency. Single-admin for v1 (multi-user/roles/MFA later).
- Passwords: pbkdf2_hmac(sha256, 200k iters), constant-time verify.
- Sessions: base64(payload).hmac — payload carries username + expiry; tamper/expiry checked.
- Session secret: random, persisted to .session_secret (0600), gitignored.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _PROJECT_ROOT / "dtm_ai.db"
_SECRET_FILE = _PROJECT_ROOT / ".session_secret"
_ITER = 200_000


# ── password hashing ────────────────────────────────────────────────────────
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _ITER)
    return f"pbkdf2_sha256${_ITER}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _algo, iters, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False  # malformed -> fail closed


# ── session signing ─────────────────────────────────────────────────────────
def _load_or_create_secret() -> bytes:
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_bytes()
    secret = secrets.token_bytes(32)
    _SECRET_FILE.write_bytes(secret)
    try:
        os.chmod(_SECRET_FILE, 0o600)
    except OSError:
        pass
    return secret


class SessionSigner:
    def __init__(self, secret: Optional[bytes] = None) -> None:
        self._secret = secret or _load_or_create_secret()

    def make(self, username: str, ttl_minutes: int) -> str:
        payload = f"{username}|{int(time.time()) + ttl_minutes * 60}"
        sig = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
        token = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        return f"{token}.{sig}"

    def verify(self, token: Optional[str]) -> Optional[str]:
        if not token or "." not in token:
            return None
        b64, _, sig = token.partition(".")
        try:
            pad = "=" * (-len(b64) % 4)
            payload = base64.urlsafe_b64decode(b64 + pad).decode()
        except Exception:
            return None
        expected = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        username, _, exp = payload.partition("|")
        if not exp.isdigit() or int(exp) < time.time():
            return None
        return username


# ── user store + admin bootstrap ─────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    pw_hash  TEXT NOT NULL,
    role     TEXT NOT NULL DEFAULT 'admin'
);
"""


class AuthStore:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._conn = sqlite3.connect(str(db_path or _DEFAULT_DB), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def ensure_admin(self, password: Optional[str]) -> Optional[str]:
        """Bootstrap a single admin. Returns a generated password to print, or None if
        an admin already exists or one was created from the supplied password."""
        cur = self._conn.execute("SELECT COUNT(*) c FROM users")
        if cur.fetchone()["c"] > 0:
            return None
        generated = None
        if not password:
            password = secrets.token_urlsafe(12)
            generated = password
        self._conn.execute(
            "INSERT INTO users(username, pw_hash, role) VALUES(?,?,?)",
            ("admin", hash_password(password), "admin"),
        )
        self._conn.commit()
        return generated

    def verify_login(self, username: str, password: str) -> Optional[str]:
        cur = self._conn.execute("SELECT pw_hash, role FROM users WHERE username=?", (username,))
        row = cur.fetchone()
        if row and verify_password(password, row["pw_hash"]):
            return row["role"]
        return None

    def set_password(self, username: str, password: str) -> None:
        self._conn.execute("UPDATE users SET pw_hash=? WHERE username=?",
                           (hash_password(password), username))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
