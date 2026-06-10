"""CredVault — encrypted, per-client, label-addressed credential store (D-25; SOP: credential-vault).

The most security-sensitive store in the system. Guarantees:
  - Encrypted at rest (Fernet/AES); key derived from a master passphrase (scrypt) and held ONLY in
    process memory after unlock(), with a TTL. Locked after a restart.
  - The AGENT can learn LABELS + field names + append-required flags (safe_list) but never a value;
    secrets are assembled by resolve() server-side for a connector's outbound call only.
  - Optional human "append": a password may embed {start_append}/{end_append}; resolve() refuses to
    assemble until a human supplies the missing piece at use-time. The append is never stored.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken

from .config import Config, get_config

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")
_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
_SENTINEL = b"dtm-credvault-ok"
_START_TOK = "{start_append}"
_END_TOK = "{end_append}"


def _safe_tenant(tenant_id: str) -> str:
    cleaned = _SAFE.sub("_", tenant_id or "").strip("._")
    return cleaned or "_unknown"


class VaultLocked(RuntimeError):
    """resolve()/read attempted while the vault key is not in memory."""


class AppendRequired(RuntimeError):
    """A credential needs a human-supplied append that wasn't provided."""
    def __init__(self, label: str, need: dict):
        super().__init__(f"credential '{label}' needs an append")
        self.label = label
        self.need = need        # {"start": bool, "end": bool}


def _fernet_key(passphrase: str, salt: bytes) -> bytes:
    raw = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt,
                         n=_SCRYPT["n"], r=_SCRYPT["r"], p=_SCRYPT["p"], dklen=_SCRYPT["dklen"])
    return base64.urlsafe_b64encode(raw)


def _write_0600(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


class CredVault:
    """One master passphrase unlocks every client's encrypted credential file."""

    def __init__(self, cfg: Optional[Config] = None, root: Optional[Path] = None) -> None:
        cfg = cfg or get_config()
        self._root = Path(root or cfg.get("DTM_VAULT_PATH") or (_PROJECT_ROOT / "vault")) / "clients"
        self._meta = self._root / ".credvault.json"
        self._ttl = int(cfg.int("DTM_CREDVAULT_TTL_MIN", 480)) * 60
        self._lock = threading.Lock()
        self._key: Optional[bytes] = None      # Fernet key bytes — memory only
        self._expires: float = 0.0

    # ── passphrase / lock state ──
    def is_initialized(self) -> bool:
        return self._meta.is_file()

    def _load_meta(self) -> dict:
        return json.loads(self._meta.read_text(encoding="utf-8"))

    def set_passphrase(self, passphrase: str) -> None:
        """First-time setup: create the salt + verifier. Refuses if already initialized."""
        if not passphrase or len(passphrase) < 8:
            raise ValueError("passphrase must be at least 8 characters")
        if self.is_initialized():
            raise ValueError("vault already initialized — use change_passphrase")
        salt = secrets.token_bytes(16)
        key = _fernet_key(passphrase, salt)
        verifier = Fernet(key).encrypt(_SENTINEL).decode("ascii")
        _write_0600(self._meta, json.dumps({"salt": base64.b64encode(salt).decode(),
                                             "kdf": "scrypt", "verifier": verifier}).encode())
        with self._lock:
            self._key = key
            self._expires = time.time() + self._ttl

    def unlock(self, passphrase: str) -> bool:
        """Derive + cache the key if the passphrase is correct. Returns False on a wrong passphrase."""
        if not self.is_initialized():
            raise ValueError("vault not initialized — set a passphrase first")
        meta = self._load_meta()
        key = _fernet_key(passphrase, base64.b64decode(meta["salt"]))
        try:
            if Fernet(key).decrypt(meta["verifier"].encode("ascii")) != _SENTINEL:
                return False
        except InvalidToken:
            return False
        with self._lock:
            self._key = key
            self._expires = time.time() + self._ttl
        return True

    def lock(self) -> None:
        with self._lock:
            self._key = None
            self._expires = 0.0

    def _live_key(self) -> bytes:
        with self._lock:
            if self._key is None:
                raise VaultLocked("credential vault is locked")
            if time.time() > self._expires:
                self._key = None
                raise VaultLocked("credential vault session expired — unlock again")
            return self._key

    def status(self) -> dict:
        with self._lock:
            unlocked = self._key is not None and time.time() <= self._expires
            return {"initialized": self.is_initialized(), "unlocked": unlocked,
                    "expires_in": int(max(0, self._expires - time.time())) if unlocked else 0,
                    "ttl_min": self._ttl // 60}

    def change_passphrase(self, old: str, new: str) -> None:
        if not self.unlock(old):
            raise ValueError("current passphrase is incorrect")
        if not new or len(new) < 8:
            raise ValueError("new passphrase must be at least 8 characters")
        # decrypt every client file with the old key, re-encrypt with the new one
        tenants = [(c["tenant"], self._read(c["tenant"])) for c in self._client_files()]
        salt = secrets.token_bytes(16)
        newkey = _fernet_key(new, salt)
        verifier = Fernet(newkey).encrypt(_SENTINEL).decode("ascii")
        _write_0600(self._meta, json.dumps({"salt": base64.b64encode(salt).decode(),
                                             "kdf": "scrypt", "verifier": verifier}).encode())
        with self._lock:
            self._key = newkey
            self._expires = time.time() + self._ttl
        for tenant, doc in tenants:
            self._write(tenant, doc)

    # ── per-client encrypted file ──
    def _cred_path(self, tenant: str) -> Path:
        return self._root / _safe_tenant(tenant) / "credentials.enc"

    def _client_files(self) -> list[dict]:
        out = []
        if self._root.is_dir():
            for d in self._root.iterdir():
                if d.is_dir() and (d / "credentials.enc").is_file():
                    out.append({"tenant": d.name})
        return out

    def _read(self, tenant: str) -> dict:
        p = self._cred_path(tenant)
        if not p.is_file():
            return {"version": 1, "creds": []}
        token = p.read_bytes()
        try:
            return json.loads(Fernet(self._live_key()).decrypt(token))
        except InvalidToken as e:
            raise VaultLocked("cannot decrypt with the current key") from e

    def _write(self, tenant: str, doc: dict) -> None:
        blob = Fernet(self._live_key()).encrypt(json.dumps(doc).encode("utf-8"))
        _write_0600(self._cred_path(tenant), blob)

    # ── append helpers ──
    @staticmethod
    def _append_need(password: str) -> dict:
        return {"start": _START_TOK in (password or ""), "end": _END_TOK in (password or "")}

    # ── agent-SAFE view (labels + field names + append flags — NEVER values) ──
    def safe_list(self, tenant: str) -> list[dict]:
        out = []
        for c in self._read(tenant).get("creds", []):
            pw = (c.get("fields") or {}).get("password", "")
            out.append({"label": c["label"],
                        "fields": sorted((c.get("fields") or {}).keys()),
                        "needs_append": self._append_need(pw),
                        "notes": c.get("notes", "")})
        return out

    # ── owner management view (fingerprints only, never raw values) ──
    def admin_list(self, tenant: str) -> list[dict]:
        out = []
        for c in self._read(tenant).get("creds", []):
            f = c.get("fields") or {}
            fp = {k: (hashlib.sha256(v.encode()).hexdigest()[:7] if v else "—") for k, v in f.items()}
            out.append({"label": c["label"], "field_names": sorted(f.keys()), "fingerprints": fp,
                        "needs_append": self._append_need(f.get("password", "")),
                        "notes": c.get("notes", ""), "updated": c.get("updated"),
                        "updated_by": c.get("updated_by")})
        return out

    def upsert(self, tenant: str, label: str, fields: dict, *, notes: str = "",
               actor: str = "owner") -> dict:
        """Create or update a credential. On UPDATE, provided fields are MERGED over the existing
        ones (the UI can't show current values, so a blank field means 'keep it') — preventing
        accidental wipes. A brand-new credential needs at least one non-empty field."""
        label = (label or "").strip().lower()
        if not _LABEL_RE.match(label):
            raise ValueError("label must be snake/kebab-case (a-z, 0-9, _ or -)")
        if not isinstance(fields, dict):
            raise ValueError("fields object required")
        clean = {k.strip(): str(v) for k, v in fields.items() if k.strip() and (v or "").strip() != ""}
        from datetime import datetime, timezone
        doc = self._read(tenant)
        existing = next((c for c in doc.get("creds", []) if c["label"] == label), None)
        if existing is None and not clean:
            raise ValueError("at least one non-empty field is required")
        merged = dict((existing or {}).get("fields") or {})
        merged.update(clean)                          # provided fields overwrite; omitted ones persist
        entry = {"label": label, "fields": merged, "notes": (notes or "").strip(),
                 "append": self._append_need(merged.get("password", "")),
                 "updated": datetime.now(timezone.utc).isoformat(), "updated_by": actor}
        creds = [c for c in doc.get("creds", []) if c["label"] != label]
        creds.append(entry)
        doc["creds"] = creds
        self._write(tenant, doc)
        return {"ok": True, "label": label}

    def delete(self, tenant: str, label: str) -> dict:
        doc = self._read(tenant)
        before = len(doc.get("creds", []))
        doc["creds"] = [c for c in doc.get("creds", []) if c["label"] != label]
        if len(doc["creds"]) == before:
            raise ValueError(f"no credential '{label}' for {tenant}")
        self._write(tenant, doc)
        return {"ok": True, "deleted": label}

    # ── resolution (server-side only; the value never returns to the agent) ──
    def resolve(self, tenant: str, label: str, *, start: str = "", end: str = "") -> dict:
        """Assemble a credential for an outbound connection. Raises AppendRequired if a needed append
        wasn't supplied. NEVER call this from a tool whose result is shown to the model."""
        for c in self._read(tenant).get("creds", []):
            if c["label"] == label:
                fields = dict(c.get("fields") or {})
                pw = fields.get("password", "")
                need = self._append_need(pw)
                if need["start"] and not start:
                    raise AppendRequired(label, need)
                if need["end"] and not end:
                    raise AppendRequired(label, need)
                if need["start"]:
                    pw = pw.replace(_START_TOK, start)
                if need["end"]:
                    pw = pw.replace(_END_TOK, end)
                if pw:
                    fields["password"] = pw
                return {"label": label, "fields": fields}
        raise ValueError(f"no credential '{label}' for {tenant}")

    def test_assemble(self, tenant: str, label: str, *, start: str = "", end: str = "") -> dict:
        """Owner 'does the append work?' check — reports a fingerprint + length, never the value."""
        r = self.resolve(tenant, label, start=start, end=end)
        pw = (r["fields"] or {}).get("password", "")
        return {"ok": True, "label": label,
                "password_len": len(pw),
                "password_fp": hashlib.sha256(pw.encode()).hexdigest()[:7] if pw else "—"}
