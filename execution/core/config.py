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
import os
import stat
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]   # .../DTM AI
_ENV_PATH = _PROJECT_ROOT / ".env"


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

    def __init__(self, env_path: Optional[Path] = None) -> None:
        self._file: dict[str, str] = {}
        path = env_path or _ENV_PATH
        if path.exists():
            _enforce_permissions(path)
            self._file = _parse_env_file(path)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        # Real environment always wins over the file.
        if key in os.environ and os.environ[key] != "":
            return os.environ[key]
        val = self._file.get(key)
        return val if (val is not None and val != "") else default

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
        _cfg = Config()
    return _cfg
