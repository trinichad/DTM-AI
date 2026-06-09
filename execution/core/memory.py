"""VaultStore — the agent's knowledge base + long-term memory, as plain markdown files.

Models Obsidian's local-first approach (D-13): everything is markdown on disk, so it's
human-editable (open the folder as an Obsidian vault), git-trackable, backup-able, and
auditable. Two roles:

  kb/                          knowledge base — runbooks/SOPs/docs.  Read via kb_search.
  clients/<tenant>/memory.md   per-client long-term memory the agent keeps (employee notebook).

IMPORTANT distinction: the "read-only by default" floor is about CLIENT SYSTEMS. Writing to
DTM AI's OWN vault is an internal, low-risk, reversible, audited action — so memory writes are
allowed by default (still visible/toggleable in the Capability Console). Never a client-system write.

Stdlib-only. Vault path from DTM_VAULT_PATH (default: <project>/vault).
"""
from __future__ import annotations

import re
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
        self.root = Path(path or cfg.get("DTM_VAULT_PATH") or (_PROJECT_ROOT / "vault"))
        self.kb_dir = self.root / "kb"
        self.clients_dir = self.root / "clients"
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

    def list_client_memories(self) -> list[str]:
        try:
            if not self.clients_dir.exists():
                return []
            return [d.name for d in sorted(self.clients_dir.iterdir()) if (d / "memory.md").exists()]
        except OSError:
            return []

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
