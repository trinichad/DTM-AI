"""SecretStore — app-managed, file-backed credential store (Invariant I-3).

Lets credentials be entered/updated from the dashboard safely:
  - persisted to `secrets.local` at mode 0600 (refuses to load if group/world-readable)
  - gitignored; never returned raw (the UI sees sha256[:7] fingerprints only)
  - writes are restricted by the CALLER to an allowlist of known integration keys, so this
    can never be used to inject arbitrary config (PATH, admin password, …)

At-rest protection here is OS file permissions + the dedicated service user (the same posture
the prior build used). Encryption-at-rest (OS keyring / SOPS / KMS) is a documented hardening
step — see architecture/secrets.md.

Precedence (see config.Config.get): process env  >  SecretStore  >  .env file  >  default.
So real env (container/systemd) still overrides, and UI-entered creds override a manual .env.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATH = _PROJECT_ROOT / "secrets.local"

_MAX_VALUE_LEN = 8192  # sanity cap


class SecretStoreError(RuntimeError):
    pass


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


class SecretStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or _DEFAULT_PATH
        self._data: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        if self.path.exists():
            if os.name == "posix":
                mode = self.path.stat().st_mode
                if mode & (stat.S_IRWXG | stat.S_IRWXO):
                    raise SecretStoreError(
                        f"{self.path} is group/world-readable (mode {oct(mode & 0o777)}); "
                        f"run `chmod 600 {self.path}`."
                    )
            self._data = _parse(self.path.read_text(encoding="utf-8"))

    def get(self, key: str) -> Optional[str]:
        return self._data.get(key)

    def all_keys(self) -> list[str]:
        return list(self._data)

    def set_many(self, values: dict[str, str], *, allowed_keys: set[str]) -> None:
        """Set/clear keys. Only `allowed_keys` may be written (caller-enforced allowlist).
        An empty-string value CLEARS that key. Persists atomically at mode 0600."""
        for key, val in values.items():
            if key not in allowed_keys:
                raise SecretStoreError(f"refusing to write non-allowlisted key '{key}'")
            if not isinstance(val, str) or len(val) > _MAX_VALUE_LEN:
                raise SecretStoreError(f"invalid value for '{key}'")
            val = val.strip()                  # trim pasted whitespace/newlines (matches _parse on read)
            if val == "":
                self._data.pop(key, None)      # empty = clear
            else:
                self._data[key] = val
        self._write()

    def _write(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        body = "# MSP AI app-managed secrets — DO NOT COMMIT. Mode 0600.\n" + \
               "".join(f"{k}={v}\n" for k, v in sorted(self._data.items()))
        # create with restrictive perms from the start
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, body.encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
