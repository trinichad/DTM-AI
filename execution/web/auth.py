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
import threading
import time
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _PROJECT_ROOT / "msp_ai.db"
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

    # ── trusted-device tokens (MFA "remember this device" — D-87 follow-up) ──
    # Scoped with a "trust|" prefix so a session token can't be replayed as a trust token (and vice
    # versa). Carries a tag derived from the user's current MFA secret, so re-enrolling/resetting MFA
    # invalidates every previously-trusted device.
    def make_trust(self, username: str, ttl_seconds: int, tag: str) -> str:
        payload = f"trust|{username}|{int(time.time()) + ttl_seconds}|{tag}"
        sig = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
        token = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        return f"{token}.{sig}"

    def verify_trust(self, token: Optional[str]) -> Optional[tuple[str, str]]:
        """Returns (username, tag) for a valid, unexpired trust token, else None."""
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
        parts = payload.split("|")
        if len(parts) != 4 or parts[0] != "trust":
            return None
        _, username, exp, tag = parts
        if not exp.isdigit() or int(exp) < time.time():
            return None
        return username, tag

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
    username    TEXT PRIMARY KEY,
    pw_hash     TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'admin',
    email       TEXT NOT NULL DEFAULT '',
    mfa_secret  TEXT NOT NULL DEFAULT '',
    mfa_enabled INTEGER NOT NULL DEFAULT 0,
    mfa_trust_days INTEGER NOT NULL DEFAULT 30
);
"""
ROLES = ("admin", "user")


class AuthStore:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        # The connection is shared across the threaded HTTP server, so every access is serialised
        # under this lock. Without it, concurrent requests corrupt cursor state and raise
        # sqlite3.InterfaceError ("bad parameter or other API misuse") on the auth path. (Mirrors
        # AuditStore/ConversationStore, which were already locked.)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path or _DEFAULT_DB), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            # migrate older dbs that predate later columns
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(users)")}
            if "email" not in cols:
                self._conn.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
            if "mfa_secret" not in cols:                       # MFA opt-in (D-87)
                self._conn.execute("ALTER TABLE users ADD COLUMN mfa_secret TEXT NOT NULL DEFAULT ''")
            if "mfa_enabled" not in cols:
                self._conn.execute("ALTER TABLE users ADD COLUMN mfa_enabled INTEGER NOT NULL DEFAULT 0")
            if "mfa_trust_days" not in cols:                   # trusted-device window (D-87 follow-up)
                self._conn.execute(
                    "ALTER TABLE users ADD COLUMN mfa_trust_days INTEGER NOT NULL DEFAULT 30")
            self._conn.commit()

    def ensure_admin(self, password: Optional[str]) -> Optional[str]:
        """Bootstrap a single admin. Returns a generated password to print, or None if
        an admin already exists or one was created from the supplied password."""
        with self._lock:
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
        with self._lock:
            cur = self._conn.execute("SELECT pw_hash, role FROM users WHERE username=?", (username,))
            row = cur.fetchone()
        if row and verify_password(password, row["pw_hash"]):
            return row["role"]
        return None

    def set_password(self, username: str, password: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE users SET pw_hash=? WHERE username=?",
                               (hash_password(password), username))
            self._conn.commit()

    # ── user CRUD (admin) + self-service ────────────────────────────────────
    def get_role(self, username: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
        return row["role"] if row else None

    def get_user(self, username: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT username, role, email, mfa_enabled, mfa_trust_days FROM users WHERE username=?",
                (username,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["mfa_enabled"] = bool(d.get("mfa_enabled"))
        return d

    def list_users(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT username, role, email, mfa_enabled FROM users ORDER BY username").fetchall()
        return [{**dict(r), "mfa_enabled": bool(r["mfa_enabled"])} for r in rows]

    def _count_admins_locked(self) -> int:
        return self._conn.execute("SELECT COUNT(*) c FROM users WHERE role='admin'").fetchone()["c"]

    def count_admins(self) -> int:
        with self._lock:
            return self._count_admins_locked()

    def create_user(self, username: str, password: str, role: str = "user", email: str = "") -> None:
        username = (username or "").strip()
        if not username or not password:
            raise ValueError("username and password are required")
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        if self.get_user(username):
            raise ValueError(f"user '{username}' already exists")
        with self._lock:
            self._conn.execute("INSERT INTO users(username, pw_hash, role, email) VALUES(?,?,?,?)",
                               (username, hash_password(password), role, email or ""))
            self._conn.commit()

    def update_user(self, username: str, *, password: Optional[str] = None,
                    role: Optional[str] = None, email: Optional[str] = None) -> None:
        if not self.get_user(username):
            raise ValueError(f"user '{username}' not found")
        with self._lock:
            if role is not None:
                if role not in ROLES:
                    raise ValueError(f"role must be one of {ROLES}")
                # don't allow demoting the last admin
                cur_role = self._conn.execute("SELECT role FROM users WHERE username=?",
                                              (username,)).fetchone()
                if (role != "admin" and cur_role and cur_role["role"] == "admin"
                        and self._count_admins_locked() <= 1):
                    raise ValueError("cannot demote the last admin")
                self._conn.execute("UPDATE users SET role=? WHERE username=?", (role, username))
            if password:
                self._conn.execute("UPDATE users SET pw_hash=? WHERE username=?",
                                   (hash_password(password), username))
            if email is not None:
                self._conn.execute("UPDATE users SET email=? WHERE username=?", (email, username))
            self._conn.commit()

    def delete_user(self, username: str) -> None:
        if not self.get_user(username):
            raise ValueError(f"user '{username}' not found")
        if self.get_role(username) == "admin" and self.count_admins() <= 1:
            raise ValueError("cannot delete the last admin")
        with self._lock:
            self._conn.execute("DELETE FROM users WHERE username=?", (username,))
            self._conn.commit()

    # ── MFA (TOTP, opt-in per user — D-87) ──────────────────────────────────
    def mfa_is_enabled(self, username: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT mfa_enabled FROM users WHERE username=?",
                                     (username,)).fetchone()
        return bool(row and row["mfa_enabled"])

    def start_mfa_setup(self, username: str) -> tuple[str, str]:
        """Generate a fresh secret, store it as PENDING (enabled stays 0 until confirmed), and
        return (secret, otpauth_uri). Re-running before confirming rotates the pending secret."""
        from . import totp
        if not self.get_user(username):
            raise ValueError(f"user '{username}' not found")
        secret = totp.generate_secret()
        with self._lock:
            self._conn.execute("UPDATE users SET mfa_secret=?, mfa_enabled=0 WHERE username=?",
                               (secret, username))
            self._conn.commit()
        return secret, totp.provisioning_uri(secret, username)

    def confirm_mfa(self, username: str, code: str) -> bool:
        """Verify a code against the PENDING secret; on success, turn MFA on. Idempotent-safe."""
        from . import totp
        with self._lock:
            row = self._conn.execute("SELECT mfa_secret FROM users WHERE username=?",
                                     (username,)).fetchone()
        secret = row["mfa_secret"] if row else ""
        if not secret or not totp.verify(secret, code):
            return False
        with self._lock:
            self._conn.execute("UPDATE users SET mfa_enabled=1 WHERE username=?", (username,))
            self._conn.commit()
        return True

    # trusted-device window: 0 = "always until signed out", else N days (D-87 follow-up)
    _TRUST_CHOICES = (0, 30, 60, 90)

    def get_mfa_trust_days(self, username: str) -> int:
        with self._lock:
            row = self._conn.execute("SELECT mfa_trust_days FROM users WHERE username=?",
                                     (username,)).fetchone()
        return int(row["mfa_trust_days"]) if row else 30

    def set_mfa_trust_days(self, username: str, days: int) -> None:
        if int(days) not in self._TRUST_CHOICES:
            raise ValueError(f"trust window must be one of {self._TRUST_CHOICES} days (0 = until sign-out)")
        with self._lock:
            self._conn.execute("UPDATE users SET mfa_trust_days=? WHERE username=?",
                               (int(days), username))
            self._conn.commit()

    def mfa_secret_tag(self, username: str) -> str:
        """A short fingerprint of the user's CURRENT MFA secret — embedded in trust tokens so a
        re-enroll/reset (new secret) invalidates every previously-trusted device. '' if no secret."""
        with self._lock:
            row = self._conn.execute("SELECT mfa_secret FROM users WHERE username=?",
                                     (username,)).fetchone()
        secret = row["mfa_secret"] if row else ""
        return hashlib.sha256(secret.encode()).hexdigest()[:12] if secret else ""

    def verify_mfa(self, username: str, code: str) -> bool:
        """Login-time check against the ACTIVE secret (only meaningful when mfa_enabled)."""
        from . import totp
        with self._lock:
            row = self._conn.execute(
                "SELECT mfa_secret, mfa_enabled FROM users WHERE username=?", (username,)).fetchone()
        if not row or not row["mfa_enabled"]:
            return False
        return totp.verify(row["mfa_secret"], code)

    def disable_mfa(self, username: str, *, code: Optional[str] = None, admin: bool = False) -> bool:
        """Turn MFA off and wipe the secret. The user must supply a valid current `code`; an
        admin reset (admin=True) skips that — the lockout-recovery path."""
        from . import totp
        with self._lock:
            row = self._conn.execute(
                "SELECT mfa_secret, mfa_enabled FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            return False
        if not admin:
            if not row["mfa_enabled"] or not totp.verify(row["mfa_secret"], code or ""):
                return False
        with self._lock:
            self._conn.execute("UPDATE users SET mfa_secret='', mfa_enabled=0 WHERE username=?",
                               (username,))
            self._conn.commit()
        return True

    def close(self) -> None:
        with self._lock:
            self._conn.close()
