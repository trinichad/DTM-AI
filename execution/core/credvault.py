"""CredVault — encrypted, per-client, label-addressed credential store (D-25/D-30; SOP: credential-vault).

The most security-sensitive store in the system. Guarantees:
  - Encrypted at rest (Fernet/AES) under one random DATA KEY (DEK). The DEK is held ONLY in
    process memory after unlock(), with a TTL. Locked after a restart (unless auto-unlock, below).
  - PER-ADMIN passphrases (D-30, key-slot model like LUKS): each admin's passphrase wraps the same
    DEK in their own slot. Any admin's passphrase unlocks; an UNLOCKED admin can set/reset any
    other admin's slot (lost-passphrase recovery) — no data re-encryption needed.
  - Optional AGENT AUTO-UNLOCK (owner toggle, default OFF): the DEK is additionally wrapped to a
    local key file (0600, gitignored), letting resolve() work unattended (e.g. a Teams request
    after a reboot). Trade-off: with it ON, at-rest protection equals file permissions — the same
    posture as the vendor keys in secrets.local. Enabling/disabling is audited.
  - The AGENT can learn LABELS + field names + append-required flags (safe_list) but never a value;
    secrets are assembled by resolve() server-side for a connector's outbound call only.
  - Optional human "append": a password may embed {start_append}/{end_append}; resolve() refuses to
    assemble until a human supplies the missing piece at use-time. The append is never stored.

v1 metas (single master passphrase) migrate in place on the first successful unlock: the old
passphrase-derived key BECOMES the DEK (so data files decrypt unchanged) and the unlocking admin
gets the first slot.
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
_SENTINEL = b"mspai-credvault-ok"
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


_shared: Optional["CredVault"] = None
_shared_lock = threading.Lock()


def get_credvault(cfg: Optional[Config] = None) -> "CredVault":
    """Process-wide shared vault (D-37) — the web API's unlock and core modules (e.g. m365_auth)
    must see the SAME in-memory DEK, so they must share one instance. Rebuilt only if the vault
    root changes (test isolation); in production the root is fixed for the process lifetime."""
    global _shared
    cfg = cfg or get_config()
    root = Path(cfg.get("MSPAI_VAULT_PATH") or (_PROJECT_ROOT / "vault")) / "clients"
    with _shared_lock:
        if _shared is None or _shared._root != root:
            _shared = CredVault(cfg)
        return _shared


class CredVault:
    """Per-admin passphrase slots wrap one data key (DEK) that encrypts every client file."""

    def __init__(self, cfg: Optional[Config] = None, root: Optional[Path] = None) -> None:
        cfg = cfg or get_config()
        self._root = Path(root or cfg.get("MSPAI_VAULT_PATH") or (_PROJECT_ROOT / "vault")) / "clients"
        self._meta = self._root / ".credvault.json"
        self._service_key_file = self._root / ".credvault.service.key"
        self._ttl = int(cfg.int("MSPAI_CREDVAULT_TTL_MIN", 480)) * 60
        self._lock = threading.Lock()
        self._key: Optional[bytes] = None      # the DEK (Fernet key bytes) — memory only
        self._expires: float = 0.0

    # ── meta / lock state ──
    def is_initialized(self) -> bool:
        return self._meta.is_file()

    def _load_meta(self) -> dict:
        return json.loads(self._meta.read_text(encoding="utf-8"))

    @staticmethod
    def _is_v1(meta: dict) -> bool:
        return "verifier" in meta and "slots" not in meta

    def _save_meta(self, meta: dict) -> None:
        _write_0600(self._meta, json.dumps(meta, indent=1).encode())

    def _cache(self, dek: bytes) -> None:
        with self._lock:
            self._key = dek
            self._expires = time.time() + self._ttl

    @staticmethod
    def _norm_user(username: str) -> str:
        u = (username or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", u):
            raise ValueError("invalid username for a vault slot")
        return u

    @staticmethod
    def _wrap_slot(passphrase: str, dek: bytes, *, by: str) -> dict:
        from datetime import datetime, timezone
        salt = secrets.token_bytes(16)
        kek = _fernet_key(passphrase, salt)
        return {"salt": base64.b64encode(salt).decode(),
                "wrapped": Fernet(kek).encrypt(dek).decode("ascii"),
                "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "updated_by": by}

    @staticmethod
    def _unwrap_slot(slot: dict, passphrase: str) -> Optional[bytes]:
        kek = _fernet_key(passphrase, base64.b64decode(slot["salt"]))
        try:
            return Fernet(kek).decrypt(slot["wrapped"].encode("ascii"))
        except InvalidToken:
            return None

    def set_passphrase(self, passphrase: str, username: str = "admin") -> None:
        """First-time setup: random DEK + a slot for the initializing admin."""
        if not passphrase or len(passphrase) < 8:
            raise ValueError("passphrase must be at least 8 characters")
        if self.is_initialized():
            raise ValueError("vault already initialized — use change_passphrase or slots")
        user = self._norm_user(username)
        dek = Fernet.generate_key()
        meta = {"version": 2, "kdf": "scrypt",
                "slots": {user: self._wrap_slot(passphrase, dek, by=user)},
                "service": {"enabled": False}}
        self._save_meta(meta)
        self._cache(dek)

    def _migrate_v1(self, meta: dict, passphrase: str, username: str) -> Optional[bytes]:
        """v1 (single master passphrase) → v2 slots. The old derived key BECOMES the DEK so the
        encrypted client files keep decrypting; the unlocking admin gets the first slot."""
        key = _fernet_key(passphrase, base64.b64decode(meta["salt"]))
        try:
            if Fernet(key).decrypt(meta["verifier"].encode("ascii")) != _SENTINEL:
                return None
        except InvalidToken:
            return None
        user = self._norm_user(username)
        self._save_meta({"version": 2, "kdf": "scrypt",
                         "slots": {user: self._wrap_slot(passphrase, key, by=f"{user} (migrated)")},
                         "service": {"enabled": False}})
        return key

    def unlock(self, passphrase: str, username: str = "admin") -> bool:
        """Unwrap the DEK with the caller's slot (their slot first, then any — a correct admin
        passphrase always unlocks). Returns False on a wrong passphrase."""
        if not self.is_initialized():
            raise ValueError("vault not initialized — set a passphrase first")
        meta = self._load_meta()
        if self._is_v1(meta):
            dek = self._migrate_v1(meta, passphrase, username)
            if dek is None:
                return False
            self._cache(dek)
            return True
        slots = meta.get("slots") or {}
        try:
            user = self._norm_user(username)
        except ValueError:
            user = ""
        order = ([user] if user in slots else []) + [u for u in slots if u != user]
        for u in order:
            dek = self._unwrap_slot(slots[u], passphrase)
            if dek is not None:
                self._cache(dek)
                return True
        return False

    def lock(self) -> None:
        with self._lock:
            self._key = None
            self._expires = 0.0

    # ── agent auto-unlock (D-30) — owner toggle; DEK wrapped to a local 0600 key file ──
    def service_unlock_enabled(self) -> bool:
        if not self.is_initialized():
            return False
        try:
            meta = self._load_meta()
        except (OSError, json.JSONDecodeError):
            return False
        return (not self._is_v1(meta)
                and bool((meta.get("service") or {}).get("enabled"))
                and self._service_key_file.is_file())

    def set_service_unlock(self, enabled: bool) -> dict:
        """Enable requires the vault UNLOCKED (the DEK must be in memory to wrap)."""
        meta = self._load_meta()
        if self._is_v1(meta):
            raise ValueError("unlock the vault once first (migrates it to per-admin passphrases)")
        if enabled:
            dek = self._live_key()
            kek = Fernet.generate_key()
            _write_0600(self._service_key_file, kek)
            meta["service"] = {"enabled": True,
                               "wrapped": Fernet(kek).encrypt(dek).decode("ascii")}
        else:
            meta["service"] = {"enabled": False}
            try:
                self._service_key_file.unlink()
            except OSError:
                pass
        self._save_meta(meta)
        return {"ok": True, "auto_unlock": enabled}

    def _try_service_unlock(self) -> bool:
        try:
            meta = self._load_meta()
            svc = meta.get("service") or {}
            if self._is_v1(meta) or not svc.get("enabled"):
                return False
            kek = self._service_key_file.read_bytes().strip()
            dek = Fernet(kek).decrypt(svc["wrapped"].encode("ascii"))
        except (OSError, KeyError, ValueError, InvalidToken, json.JSONDecodeError):
            return False
        self._cache(dek)
        return True

    def _live_key(self) -> bytes:
        with self._lock:
            if self._key is not None and time.time() <= self._expires:
                return self._key
            self._key = None
        # expired or never unlocked — the service key file may revive it unattended
        if self._try_service_unlock():
            with self._lock:
                if self._key is not None:
                    return self._key
        raise VaultLocked("credential vault is locked")

    # ── per-admin slots (D-30) ──
    def slots(self) -> list[dict]:
        """Slot owners + metadata — no unlock required (names only, never key material)."""
        if not self.is_initialized():
            return []
        try:
            meta = self._load_meta()
        except (OSError, json.JSONDecodeError):
            return []
        if self._is_v1(meta):
            return [{"username": "(legacy master passphrase)", "legacy": True}]
        return [{"username": u, "updated": s.get("updated"), "updated_by": s.get("updated_by")}
                for u, s in sorted((meta.get("slots") or {}).items())]

    def set_slot(self, username: str, passphrase: str, *, by: str) -> dict:
        """Create/reset an admin's slot — wraps the in-memory DEK, so the vault must be UNLOCKED.
        This is how a lost passphrase is recovered: another admin unlocks, then resets the slot."""
        if not passphrase or len(passphrase) < 8:
            raise ValueError("passphrase must be at least 8 characters")
        user = self._norm_user(username)
        dek = self._live_key()
        meta = self._load_meta()
        if self._is_v1(meta):
            raise ValueError("unlock the vault once first (migrates it to per-admin passphrases)")
        meta.setdefault("slots", {})[user] = self._wrap_slot(passphrase, dek, by=by)
        self._save_meta(meta)
        return {"ok": True, "username": user}

    def delete_slot(self, username: str) -> dict:
        """Remove an admin's slot (vault must be unlocked). The LAST slot can never be removed —
        that would orphan the data behind only the auto-unlock key file."""
        user = self._norm_user(username)
        self._live_key()                       # unlocked required
        meta = self._load_meta()
        slots = meta.get("slots") or {}
        if user not in slots:
            raise ValueError(f"no vault slot for '{user}'")
        if len(slots) == 1:
            raise ValueError("cannot remove the last passphrase slot")
        del slots[user]
        self._save_meta(meta)
        return {"ok": True, "removed": user}

    def status(self) -> dict:
        with self._lock:
            cached = self._key is not None and time.time() <= self._expires
            expires_in = int(max(0, self._expires - time.time())) if cached else 0
        auto = self.service_unlock_enabled()
        return {"initialized": self.is_initialized(),
                "unlocked": cached or auto,            # auto-unlock revives the key on demand
                "key_cached": cached,
                "auto_unlock": auto,
                "expires_in": expires_in,
                "ttl_min": self._ttl // 60,
                "slots": self.slots()}

    def change_passphrase(self, old: str, new: str, username: str = "admin") -> None:
        """Change the CALLER's own passphrase (their slot must accept the old one).
        The DEK is unchanged, so nothing is re-encrypted."""
        if not new or len(new) < 8:
            raise ValueError("new passphrase must be at least 8 characters")
        if not self.is_initialized():
            raise ValueError("vault not initialized")
        meta = self._load_meta()
        if self._is_v1(meta):
            dek = self._migrate_v1(meta, old, username)
            if dek is None:
                raise ValueError("current passphrase is incorrect")
            self._cache(dek)
            self.set_slot(username, new, by=username)
            return
        user = self._norm_user(username)
        slot = (meta.get("slots") or {}).get(user)
        if slot is None:
            raise ValueError(f"no vault slot for '{user}' — ask another admin to set one for you")
        dek = self._unwrap_slot(slot, old)
        if dek is None:
            raise ValueError("current passphrase is incorrect")
        self._cache(dek)
        self.set_slot(user, new, by=user)

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
