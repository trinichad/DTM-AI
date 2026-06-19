"""Secure config loader (Invariant I-3).

- Loads .env (simple parser) layered under real process env (env wins).
- Refuses to boot if .env is group/world-readable on POSIX (mirrors OpenSSH key checks).
- Never prints secrets: fingerprint(value) -> sha256[:7] is the only exposure.
- Fail-closed: require(key) raises if a key is absent/empty.

Stdlib-only. python-dotenv is NOT required (we parse a minimal .env ourselves), so
the core boots even before deps are installed.
"""
from __future__ import annotations

import hashlib
import logging
import os
import stat
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]   # .../MSP AI
_ENV_PATH = _PROJECT_ROOT / ".env"

_log = logging.getLogger("mspai.config")


class ConfigError(RuntimeError):
    pass


def _enforce_permissions(path: Path) -> None:
    if os.name != "posix":
        return
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(
            f"{path} is group/world-accessible (mode {oct(mode & 0o777)}). "
            f"Run `chmod 600 {path}` — refusing to load secrets from a readable file."
        )


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        # strip trailing inline comment only when value was unquoted
        if val and " #" in val and not (raw.strip().endswith('"') or raw.strip().endswith("'")):
            val = val.split(" #", 1)[0].rstrip()
        out[key] = val
    return out


class Config:
    """Layered config: process env over .env file. Secrets never logged."""

    def __init__(self, env_path: Optional[Path] = None, secret_store=None) -> None:
        self._file: dict[str, str] = {}
        path = env_path or _ENV_PATH
        if path.exists():
            _enforce_permissions(path)
            self._file = _parse_env_file(path)
        self._secrets = secret_store  # optional SecretStore (app-managed, UI-entered)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        # Precedence: real environment > app-managed SecretStore > .env file > default.
        if key in os.environ and os.environ[key] != "":
            return os.environ[key]
        if self._secrets is not None:
            sv = self._secrets.get(key)
            if sv:
                return sv
        val = self._file.get(key)
        return val if (val is not None and val != "") else default

    @property
    def secrets(self):
        return self._secrets

    def require(self, key: str) -> str:
        val = self.get(key)
        if not val:
            raise ConfigError(f"required config '{key}' is not set (fail-closed)")
        return val

    def bool(self, key: str, default: bool = False) -> bool:
        val = self.get(key)
        if val is None:
            return default
        return val.strip().lower() in ("1", "true", "yes", "on")

    def int(self, key: str, default: int) -> int:
        val = self.get(key)
        try:
            return int(val) if val is not None else default
        except ValueError:
            return default

    def present(self, key: str) -> bool:
        return bool(self.get(key))


def fingerprint(value: Optional[str]) -> str:
    """sha256(value)[:7] — verify the right secret is configured without exposing it."""
    if not value:
        return "—"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:7]


# Process-wide singleton (cheap; re-instantiable in tests with a custom env_path).
_cfg: Optional[Config] = None


def get_config() -> Config:
    global _cfg
    if _cfg is None:
        # Attach the app-managed SecretStore so UI-entered credentials are visible to
        # require()/clients process-wide. Imported lazily to avoid an import cycle.
        store = None
        try:
            from .secrets_store import SecretStore
            store = SecretStore()
        except Exception as exc:
            # A failure HERE silently blanks every UI-entered credential at once (the store
            # is the only source for them), so it must be loud — never a clean empty store.
            # The usual culprit is secrets.local being unreadable by the service user (e.g.
            # a root-owned rewrite): `chown <service-user> secrets.local && chmod 600`.
            secrets_path = _PROJECT_ROOT / "secrets.local"
            if secrets_path.exists():
                _log.error(
                    "SecretStore unavailable (%s: %s) — every UI-entered credential will read "
                    "as MISSING. Check ownership/permissions on %s; the service user must be "
                    "able to read it (chmod 600, owned by the service user).",
                    type(exc).__name__, exc, secrets_path,
                )
            else:
                # No file yet (fresh install, or creds only in .env) — degrade quietly.
                _log.warning("No SecretStore (%s: %s); using .env + process env only.",
                             type(exc).__name__, exc)
        _cfg = Config(secret_store=store)
    return _cfg
