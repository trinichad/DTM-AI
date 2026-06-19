"""Learned skills as PLAYBOOKS — reusable procedures composed from already-trusted tools (D-15).

A learned skill is NOT new code: it's a saved procedure that sequences tools the owner already
trusts (per D-15 / the D-4 reframe). So it can never invent new access — the security boundary
stays at the primitive / Capability-Console layer. Playbooks are markdown files in the vault
(`<vault>/skills/<slug>.md`): human-editable, git-trackable, backup-able — same philosophy as the
memory vault (D-13).

Two uses:
  - `search()` powers the `skill_search` tool so the agent checks for an existing playbook BEFORE
    re-deriving a procedure.
  - `save()` is owner-confirmed and DEDUPS (slug collision or strong term overlap) so the same
    skill is never created twice.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import Config, get_config

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "with", "by", "this",
         "that", "is", "are", "use", "using", "when", "client", "clients", "mspai", "all"}


def _slug(name: str) -> str:
    s = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return s or "skill"


def _terms(*texts: str) -> set[str]:
    words = re.findall(r"[a-z0-9_]+", " ".join(texts).lower())
    return {w for w in words if len(w) > 2 and w not in _STOP}


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse simple `--- key: value ---` frontmatter; return (fields, body)."""
    out: dict[str, str] = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return out, text
    body_start = len(lines)
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = i + 1
            break
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip().lower()] = v.strip().strip('"').strip("'")
    return out, "\n".join(lines[body_start:]).strip()


def _csv(v: str) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


class PlaybookStore:
    """Saved learned-skill playbooks under <vault>/skills/*.md (stdlib-only, tolerant of a missing dir)."""

    def __init__(self, path: Optional[Path] = None, cfg: Optional[Config] = None) -> None:
        cfg = cfg or get_config()
        vault = Path(path) if path else Path(cfg.get("MSPAI_VAULT_PATH") or (_PROJECT_ROOT / "vault"))
        self.root = vault / "skills"

    @property
    def available(self) -> bool:
        return True                      # the library exists logically; the dir is created on save

    def _path(self, slug: str) -> Path:
        return self.root / f"{slug}.md"

    def _parse(self, md: Path) -> dict:
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            return {}
        fm, body = _frontmatter(text)
        slug = md.stem
        return {
            "slug": slug,
            "name": fm.get("name") or slug.replace("-", " ").title(),
            "category": fm.get("category") or "general",
            "description": fm.get("description") or "",
            "tools": _csv(fm.get("tools", "")),
            "tags": _csv(fm.get("tags", "")),
            "created_by": fm.get("created_by") or "",
            "created": fm.get("created") or "",
            "body": body,
            "path": slug,
        }

    def list_skills(self) -> list[dict]:
        try:
            files = sorted(self.root.glob("*.md"))
        except OSError:
            return []
        out = [s for s in (self._parse(p) for p in files) if s]
        out.sort(key=lambda s: (s["category"], s["name"]))
        return out

    def get(self, slug: str) -> Optional[dict]:
        p = self._path(_slug(slug))
        return self._parse(p) if p.is_file() else None

    def search(self, query: str, limit: int = 5) -> list[dict]:
        q = _terms(query)
        if not q:
            return []
        scored = []
        for s in self.list_skills():
            hay = _terms(s["name"], s["description"], " ".join(s["tags"]), " ".join(s["tools"]))
            score = len(q & hay)
            if score:
                scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:max(1, limit)]]

    def find_duplicate(self, name: str, description: str = "") -> Optional[dict]:
        """A near-duplicate already-saved skill, or None. Slug collision = duplicate; otherwise a
        strong term overlap (Jaccard >= 0.6, or one set subsumes the other) on name+description."""
        existing = self.get(_slug(name))
        if existing:
            return existing
        want = _terms(name, description)
        if not want:
            return None
        for s in self.list_skills():
            have = _terms(s["name"], s["description"])
            if not have:
                continue
            jac = len(want & have) / len(want | have)
            if jac >= 0.6 or want <= have or have <= want:
                return s
        return None

    def save(self, name: str, description: str = "", tools=None, when: str = "",
             steps: str = "", tags=None, created_by: str = "msp-ai", force: bool = False) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("skill name required")
        if not force:
            dup = self.find_duplicate(name, description)
            if dup:
                return {"ok": False, "duplicate": dup}      # already exists — don't create a twin
        slug = _slug(name)
        tools = tools or []
        tags = tags or []
        self.root.mkdir(parents=True, exist_ok=True)
        head = [
            "---",
            f"name: {name}",
            f"description: {description.strip()}",
            f"tools: {', '.join(tools)}",
            f"tags: {', '.join(tags)}",
            f"created_by: {created_by}",
            f"created: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            "---",
            "",
        ]
        body: list[str] = []
        if when.strip():
            body += ["# When to use", when.strip(), ""]
        if steps.strip():
            body += ["# Steps", steps.strip(), ""]
        self._path(slug).write_text("\n".join(head + body), encoding="utf-8")
        return {"ok": True, "slug": slug, "skill": self.get(slug)}

    def delete(self, slug: str) -> dict:
        p = self._path(_slug(slug))
        if not p.is_file():
            raise ValueError(f"unknown skill {slug}")
        p.unlink()
        return {"ok": True, "deleted": _slug(slug)}
