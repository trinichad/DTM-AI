"""VaultStore — the agent's knowledge base + long-term memory, as plain markdown files.

Models Obsidian's local-first approach (D-13): everything is markdown on disk, so it's
human-editable (open the folder as an Obsidian vault), git-trackable, backup-able, and
auditable. Two roles:

  kb/                          knowledge base — runbooks/SOPs/docs.  Read via kb_search.
  clients/<tenant>/memory.md   per-client long-term memory — a LIVING record of the client's
                               current environment. Read AND updated (corrected/edited/pruned) as
                               things change; not an append-only log. One-step backup on overwrite.

IMPORTANT distinction: the "read-only by default" floor is about CLIENT SYSTEMS. Writing to
MSP AI's OWN vault is an internal, low-risk, reversible, audited action — so memory writes are
allowed by default (still visible/toggleable in the Capability Console). Never a client-system write.

Stdlib-only. Vault path from MSPAI_VAULT_PATH (default: <project>/vault).
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import Config, get_config

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_tenant(tenant_id: str) -> str:
    """Sanitize a tenant id into a safe path segment (no traversal)."""
    cleaned = _SAFE.sub("_", tenant_id or "").strip("._")
    return cleaned or "_unknown"


class VaultStore:
    def __init__(self, path: Optional[Path] = None, cfg: Optional[Config] = None) -> None:
        cfg = cfg or get_config()
        self.root = Path(path or cfg.get("MSPAI_VAULT_PATH") or (_PROJECT_ROOT / "vault"))
        self.kb_dir = self.root / "kb"
        self.clients_dir = self.root / "clients"
        self.users_dir = self.root / "users"     # per-USER profile memory (D-31)
        # Bundled, version-controlled reference docs that ship WITH the app (vendor API/command
        # references etc.). Searched alongside the vault's kb/ so the assistant always has them,
        # no per-server file copying. The vault's kb/ remains for the owner's own runbooks/notes.
        self.reference_dir = _PROJECT_ROOT / "reference"

    # ── knowledge base (read) ──
    def _kb_files(self) -> list[tuple[Path, Path]]:
        """All searchable docs as (file, base) pairs (base used to compute the display doc id).
        Covers the vault kb/ (owner runbooks) AND the bundled reference/ (vendor references)."""
        out: list[tuple[Path, Path]] = []
        for base, root in ((self.kb_dir, self.root), (self.reference_dir, _PROJECT_ROOT)):
            try:
                if base.exists():
                    out.extend((p, root) for p in sorted(base.rglob("*.md")))
            except OSError:
                continue
        return out

    def search_kb(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        terms = [t for t in re.split(r"\s+", query.lower().strip()) if t]
        if not terms:
            return []
        hits: list[dict[str, Any]] = []
        for md, base in self._kb_files():
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                continue
            low = text.lower()
            if not all(t in low for t in terms):
                continue
            score = sum(low.count(t) for t in terms)
            snippet = self._snippet(text, terms)
            hits.append({"doc": str(md.relative_to(base)), "score": score, "snippet": snippet})
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:limit]

    @staticmethod
    def _snippet(text: str, terms: list[str], width: int = 240) -> str:
        low = text.lower()
        pos = min((low.find(t) for t in terms if low.find(t) >= 0), default=0)
        start = max(0, pos - 60)
        return text[start:start + width].strip().replace("\n", " ")

    # ── long-term memory (read + internal write) ──
    def _memory_path(self, tenant_id: str) -> Path:
        return self.clients_dir / _safe_tenant(tenant_id) / "memory.md"

    def read_memory(self, tenant_id: str) -> str:
        # tolerate a missing/inaccessible vault (e.g. ProtectHome) -> empty, never 500
        try:
            path = self._memory_path(tenant_id)
            return path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError:
            return ""

    def list_kb(self) -> list[str]:
        return [str(p.relative_to(base)) for p, base in self._kb_files()]

    def read_kb_doc(self, doc: str) -> Optional[str]:
        """Return a KB/reference doc's content by its listed id (the path list_kb returns).
        Only serves files already enumerated by _kb_files() → no path traversal."""
        for md, base in self._kb_files():
            try:
                if str(md.relative_to(base)) == doc:
                    return md.read_text(encoding="utf-8")
            except (OSError, ValueError):
                continue
        return None

    def write_kb_doc(self, name: str, content: str) -> dict[str, Any]:
        """Create/overwrite a KB doc under vault/kb/ (the owner's runbooks). Traversal-safe; always
        lands under kb/. Bundled reference/ docs are NOT writable here (they ship with the app)."""
        name = (name or "").strip().lstrip("/")
        if not name or ".." in name:
            return {"error": "invalid doc name"}
        if not name.endswith(".md"):
            name += ".md"
        target = self.kb_dir / name
        try:
            kb = self.kb_dir.resolve()
            rt = target.resolve()
            if rt != kb and not str(rt).startswith(str(kb) + "/"):
                return {"error": "doc must live under kb/"}
        except OSError:
            return {"error": "bad path"}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content or "", encoding="utf-8")
        return {"ok": True, "doc": str(target.relative_to(self.root))}

    def delete_kb_doc(self, doc: str) -> dict[str, Any]:
        """Delete one of the owner's kb/ docs. Refuses bundled reference/ docs (read-only)."""
        if not doc or doc.startswith("reference/"):
            return {"error": "only your own kb/ docs can be deleted (reference docs ship with the app)"}
        try:
            kb = self.kb_dir.resolve()
            rt = (self.root / doc).resolve()
            if not (rt.is_file() and str(rt).startswith(str(kb) + "/")):
                return {"error": "doc not found under kb/"}
        except OSError:
            return {"error": "bad path"}
        rt.unlink()
        return {"ok": True, "deleted": doc}

    def rename_kb_doc(self, src: str, dst: str) -> dict[str, Any]:
        """Rename/move one of the owner's kb/ docs. Refuses bundled reference/ docs (read-only),
        traversal, and clobbering an existing doc. Atomic (os.rename)."""
        if not src or src.startswith("reference/"):
            return {"error": "only your own kb/ docs can be renamed (reference docs ship with the app)"}
        dst = (dst or "").strip().lstrip("/")
        if not dst or ".." in dst:
            return {"error": "invalid new name"}
        if not dst.endswith(".md"):
            dst += ".md"
        src_path = self.root / src
        dst_path = self.kb_dir / dst
        try:
            kb = self.kb_dir.resolve()
            s = src_path.resolve()
            d = dst_path.resolve()
            if not (s.is_file() and str(s).startswith(str(kb) + "/")):
                return {"error": "doc not found under kb/"}
            if d != kb and not str(d).startswith(str(kb) + "/"):
                return {"error": "doc must live under kb/"}
            if d.exists():
                return {"error": "a doc with that name already exists"}
        except OSError:
            return {"error": "bad path"}
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        src_path.rename(dst_path)
        return {"ok": True, "from": src, "to": str(dst_path.relative_to(self.root))}

    def list_client_memories(self) -> list[str]:
        try:
            if not self.clients_dir.exists():
                return []
            return [d.name for d in sorted(self.clients_dir.iterdir()) if (d / "memory.md").exists()]
        except OSError:
            return []

    def list_clients(self) -> list[str]:
        """All registered clients (every dir under clients/, with or without saved memory yet)."""
        try:
            return [d.name for d in sorted(self.clients_dir.iterdir()) if d.is_dir()]
        except OSError:
            return []

    def add_client(self, client_id: str) -> dict[str, Any]:
        """Register a client (tenant) so it can be selected. Creates clients/<id>/. Idempotent."""
        if (client_id or "").strip() in ("", "*"):
            return {"error": "invalid client id"}
        cid = _safe_tenant(client_id)
        if cid in ("", "_unknown"):
            return {"error": "invalid client id"}
        (self.clients_dir / cid).mkdir(parents=True, exist_ok=True)
        return {"ok": True, "id": cid}

    def remove_client(self, client_id: str) -> dict[str, Any]:
        """Remove a client and its saved memory (clients/<id>/). Destructive."""
        cid = _safe_tenant(client_id)
        d = self.clients_dir / cid
        if not d.is_dir():
            return {"error": "unknown client"}
        shutil.rmtree(d)
        return {"ok": True, "removed": cid}

    def append_memory(self, tenant_id: str, note: str, actor: str) -> dict[str, Any]:
        if tenant_id in ("", "*"):
            return {"error": "memory requires a specific client (tenant), not '*'"}
        note = note.strip()
        if not note:
            return {"error": "empty note"}
        path = self._memory_path(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(f"# Memory — {tenant_id}\n\n", encoding="utf-8")
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"- {ts} ({actor}): {note}\n")
        return {"ok": True, "appended": note, "doc": str(path.relative_to(self.root))}

    # ── per-USER profile memory (D-31) — what the agent knows about each human ──
    def _user_memory_path(self, username: str) -> Path:
        return self.users_dir / f"{_safe_tenant(username)}.md"

    def read_user_memory(self, username: str) -> str:
        try:
            p = self._user_memory_path(username)
            return p.read_text(encoding="utf-8") if p.exists() else ""
        except OSError:
            return ""

    def append_user_memory(self, username: str, note: str, actor: str) -> dict[str, Any]:
        if not (username or "").strip():
            return {"error": "no user bound to this conversation"}
        note = (note or "").strip()
        if not note:
            return {"error": "empty note"}
        p = self._user_memory_path(username)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(f"# Profile — {username}\n\n", encoding="utf-8")
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with p.open("a", encoding="utf-8") as f:
            f.write(f"- {ts} ({actor}): {note}\n")
        return {"ok": True, "appended": note, "doc": str(p.relative_to(self.root))}

    def write_user_memory(self, username: str, content: str, actor: str) -> dict[str, Any]:
        """Overwrite a user's profile memory (revise/correct/prune). .bak kept for rollback."""
        if not (username or "").strip():
            return {"error": "no user bound to this conversation"}
        p = self._user_memory_path(username)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            try:
                shutil.copy2(p, p.parent / (p.name + ".bak"))
            except OSError:
                pass
        body = (content or "").rstrip() + "\n"
        p.write_text(body, encoding="utf-8")
        return {"ok": True, "doc": str(p.relative_to(self.root)), "bytes": len(body)}

    def write_memory(self, tenant_id: str, content: str, actor: str) -> dict[str, Any]:
        """Overwrite a client's long-term memory with a revised version. Memory is a LIVING record
        of the client's current environment, not an append-only log: facts get corrected, updated,
        or removed as things change (firewall swapped, machines replaced, a contact leaves). The
        prior version is kept as memory.md.bak for one-step rollback. Internal vault write — never a
        client-system change. Rejects '*' (a single client only)."""
        if tenant_id in ("", "*"):
            return {"error": "memory requires a specific client (tenant), not '*'"}
        path = self._memory_path(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():                                  # rolling one-step backup before overwrite
            try:
                shutil.copy2(path, path.parent / (path.name + ".bak"))
            except OSError:
                pass
        body = (content or "").rstrip() + "\n"
        path.write_text(body, encoding="utf-8")
        return {"ok": True, "doc": str(path.relative_to(self.root)), "bytes": len(body)}
