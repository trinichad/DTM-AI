"""Custom integrations (D-27) — owner-defined integration METADATA, no secrets.

A record describes a vendor connection the owner built in the dashboard: its credential
fields (each derives an env key `<ID>_<FIELD-SLUG>` that lives in the SecretStore exactly
like built-in integrations), the API base URL, how auth is applied on the wire, and which
URL path prefixes are readable via scoped_read (empty = fail closed).

Storage is `<vault>/integrations.json` — plain JSON on disk: git-trackable (I-6),
human-editable, and NEVER contains a secret value (I-3). Stdlib-only.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import Config, get_config

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_SLUG_RE = re.compile(r"[^A-Z0-9]+")

AUTH_TYPES = ("bearer", "basic", "header", "query", "none")
AUTH_KINDS = ("api_key", "login", "custom")

# ids that can never be claimed by a custom integration (built-ins + local cards + reserved)
RESERVED_IDS = {"kaseya", "cylance", "huntress", "anthropic", "openai", "openai_codex",
                "email", "msteams", "obsidian", "skills", "msp_ai", "custom", "ollama"}


def field_key(integration_id: str, label: str) -> str:
    """Derive the env-style key for an owner-named field: ('sop_kb', 'API key') -> 'SOP_KB_API_KEY'."""
    slug = _SLUG_RE.sub("_", (label or "").upper()).strip("_")
    return f"{integration_id.upper()}_{slug or 'VALUE'}"


@dataclass
class CustomIntegration:
    id: str
    label: str
    auth_kind: str = "api_key"                  # builder-form template hint only
    fields: list[dict] = field(default_factory=list)   # [{key,label,required,secret}]
    base_url: str = ""
    auth: dict = field(default_factory=dict)    # {type, field, user_field, pass_field, name}
    read_paths: list[str] = field(default_factory=list)
    probe_path: str = ""
    docs_url: str = ""
    notes: str = ""
    verify_tls: bool = True                      # False = trust self-signed cert (local/LAN devices)
    created: str = ""
    updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in (
            "id", "label", "auth_kind", "fields", "base_url", "auth", "read_paths",
            "probe_path", "docs_url", "notes", "verify_tls", "created", "updated")}

    @property
    def required_keys(self) -> tuple[str, ...]:
        return tuple(f["key"] for f in self.fields if f.get("required"))

    @property
    def optional_keys(self) -> tuple[str, ...]:
        return tuple(f["key"] for f in self.fields if not f.get("required"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate(rec: dict[str, Any], *, is_update: bool = False) -> CustomIntegration:
    """Normalize + validate an incoming record. Raises ValueError with a human message."""
    cid = str(rec.get("id") or "").strip().lower()
    if not _ID_RE.match(cid):
        raise ValueError("id must be snake_case (start with a letter; letters/digits/_; ≤40 chars)")
    if cid in RESERVED_IDS:
        raise ValueError(f"'{cid}' is a reserved integration name")
    label = str(rec.get("label") or "").strip() or cid.replace("_", " ").title()
    auth_kind = str(rec.get("auth_kind") or "api_key").strip()
    if auth_kind not in AUTH_KINDS:
        raise ValueError(f"auth_kind must be one of {AUTH_KINDS}")

    fields_in = rec.get("fields") or []
    if not isinstance(fields_in, list) or not fields_in:
        raise ValueError("at least one credential field is required")
    fields_out: list[dict] = []
    seen: set[str] = set()
    for f in fields_in[:12]:
        flabel = str((f or {}).get("label") or "").strip()
        if not flabel:
            raise ValueError("every field needs a label")
        key = field_key(cid, flabel)
        if key in seen:
            raise ValueError(f"duplicate field label '{flabel}'")
        seen.add(key)
        fields_out.append({"key": key, "label": flabel,
                           "required": bool(f.get("required", True)),
                           "secret": bool(f.get("secret", True))})

    base_url = str(rec.get("base_url") or "").strip().rstrip("/")
    if base_url and not base_url.startswith("https://"):
        raise ValueError("base_url must be https://")

    auth_in = rec.get("auth") or {}
    atype = str(auth_in.get("type") or "none").strip()
    if atype not in AUTH_TYPES:
        raise ValueError(f"auth.type must be one of {AUTH_TYPES}")
    auth: dict[str, Any] = {"type": atype}
    def _field_ref(name: str) -> str:
        v = str(auth_in.get(name) or "").strip()
        if v and v not in seen:
            raise ValueError(f"auth.{name} must reference one of this integration's field keys")
        return v
    if atype == "bearer":
        auth["field"] = _field_ref("field") or (fields_out[0]["key"] if fields_out else "")
    elif atype == "basic":
        auth["user_field"] = _field_ref("user_field")
        auth["pass_field"] = _field_ref("pass_field")
        if not (auth["user_field"] and auth["pass_field"]):
            raise ValueError("basic auth needs auth.user_field and auth.pass_field")
    elif atype in ("header", "query"):
        name = str(auth_in.get("name") or "").strip()
        if not name or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
            raise ValueError(f"{atype} auth needs a simple header/param name")
        auth["name"] = name
        auth["field"] = _field_ref("field") or (fields_out[0]["key"] if fields_out else "")

    read_paths: list[str] = []
    for p in (rec.get("read_paths") or [])[:32]:
        p = str(p or "").strip()
        if not p:
            continue
        if not p.startswith("/") or "://" in p or ".." in p or p.startswith("//"):
            raise ValueError(f"read path '{p}' must start with '/' and contain no scheme/'..'")
        read_paths.append(p)

    probe_path = str(rec.get("probe_path") or "").strip()
    if probe_path and (not probe_path.startswith("/") or ".." in probe_path or "://" in probe_path):
        raise ValueError("probe_path must be a simple '/...' path")

    return CustomIntegration(
        id=cid, label=label, auth_kind=auth_kind, fields=fields_out, base_url=base_url,
        auth=auth, read_paths=read_paths, probe_path=probe_path,
        docs_url=str(rec.get("docs_url") or "").strip(),
        notes=str(rec.get("notes") or "").strip(),
        verify_tls=bool(rec.get("verify_tls", True)))


class CustomIntegrationStore:
    """File-backed store for the owner's custom integration records (metadata only)."""

    def __init__(self, path: Optional[Path] = None, cfg: Optional[Config] = None) -> None:
        cfg = cfg or get_config()
        root = Path(cfg.get("MSPAI_VAULT_PATH") or (_PROJECT_ROOT / "vault"))
        self.path = path or (root / "integrations.json")
        self._lock = threading.Lock()

    # ── persistence ──
    def _load(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    # ── reads ──
    @staticmethod
    def _from_rec(rec: dict) -> CustomIntegration:
        known = {k: rec[k] for k in (
            "id", "label", "auth_kind", "fields", "base_url", "auth", "read_paths",
            "probe_path", "docs_url", "notes", "verify_tls", "created", "updated") if k in rec}
        return CustomIntegration(**known)

    def all(self) -> list[CustomIntegration]:
        return [self._from_rec(rec) for rec in sorted(self._load().values(),
                                                      key=lambda r: r.get("id", ""))]

    def get(self, cid: str) -> Optional[CustomIntegration]:
        rec = self._load().get((cid or "").strip().lower())
        return self._from_rec(rec) if rec else None

    # ── mutations (caller audits) ──
    def create(self, rec: dict[str, Any]) -> CustomIntegration:
        ci = _validate(rec)
        with self._lock:
            data = self._load()
            if ci.id in data:
                raise ValueError(f"integration '{ci.id}' already exists")
            ci.created = ci.updated = _now()
            data[ci.id] = ci.to_dict()
            self._save(data)
        return ci

    def update(self, cid: str, rec: dict[str, Any]) -> CustomIntegration:
        cid = (cid or "").strip().lower()
        with self._lock:
            data = self._load()
            if cid not in data:
                raise ValueError(f"unknown integration '{cid}'")
            ci = _validate({**data[cid], **rec, "id": cid}, is_update=True)
            ci.created = data[cid].get("created") or _now()
            ci.updated = _now()
            data[cid] = ci.to_dict()
            self._save(data)
        return ci

    def rename(self, cid: str, new_id: str) -> tuple[CustomIntegration, dict[str, str]]:
        """Change an integration's id. Returns (record, key_map old->new) so the caller can
        migrate stored secret values server-side."""
        cid = (cid or "").strip().lower()
        new_id = (new_id or "").strip().lower()
        if not _ID_RE.match(new_id):
            raise ValueError("new id must be snake_case")
        if new_id in RESERVED_IDS:
            raise ValueError(f"'{new_id}' is a reserved integration name")
        with self._lock:
            data = self._load()
            if cid not in data:
                raise ValueError(f"unknown integration '{cid}'")
            if new_id in data:
                raise ValueError(f"integration '{new_id}' already exists")
            rec = data.pop(cid)
            key_map: dict[str, str] = {}
            for f in rec.get("fields") or []:
                old_key = f["key"]
                f["key"] = field_key(new_id, f["label"])
                key_map[old_key] = f["key"]
            auth = rec.get("auth") or {}
            for k in ("field", "user_field", "pass_field"):
                if auth.get(k) in key_map:
                    auth[k] = key_map[auth[k]]
            rec["id"], rec["auth"], rec["updated"] = new_id, auth, _now()
            data[new_id] = rec
            self._save(data)
        return self._from_rec(rec), key_map

    def delete(self, cid: str) -> CustomIntegration:
        cid = (cid or "").strip().lower()
        with self._lock:
            data = self._load()
            if cid not in data:
                raise ValueError(f"unknown integration '{cid}'")
            rec = data.pop(cid)
            self._save(data)
        return self._from_rec(rec)


# Process-wide accessor (cheap to re-read; the store re-loads the file on every read so
# external edits — git checkout, hand-edit — are picked up without a restart).
_store: Optional[CustomIntegrationStore] = None


def get_store() -> CustomIntegrationStore:
    global _store
    if _store is None:
        _store = CustomIntegrationStore()
    return _store
