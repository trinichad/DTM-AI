"""HermesSkillsReader — surface Hermes' learned skills in the DTM AI dashboard (read-only).

Hermes persists skills as `SKILL.md` files under ~/.hermes/skills/<category>/<skill>/ (built-in,
Hub-installed, and agent-created all land here). This reader walks that tree and parses each
skill's frontmatter so the owner gets one pane of glass instead of SSH-ing to run `hermes skills list`.

Read-only by design: DTM AI never edits Hermes' skills here. Path is DTM_HERMES_SKILLS_DIR or
~/.hermes/skills. Stdlib-only; tolerant of a missing dir (returns empty -> clean UI empty state).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .config import Config, get_config


def _default_dir(cfg: Config) -> Path:
    override = cfg.get("DTM_HERMES_SKILLS_DIR")
    return Path(override).expanduser() if override else Path.home() / ".hermes" / "skills"


def _frontmatter(text: str) -> dict[str, str]:
    """Parse simple `--- key: value ---` frontmatter (no YAML dep)."""
    out: dict[str, str] = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return out
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip().lower()] = v.strip().strip('"').strip("'")
    return out


def _first_paragraph(text: str) -> str:
    body = text
    if text.lstrip().startswith("---"):
        parts = text.split("---", 2)
        body = parts[2] if len(parts) == 3 else text
    for line in body.splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return s[:200]
    return ""


class HermesSkillsReader:
    def __init__(self, path: Optional[Path] = None, cfg: Optional[Config] = None) -> None:
        cfg = cfg or get_config()
        self.root = Path(path).expanduser() if path else _default_dir(cfg)

    @property
    def available(self) -> bool:
        return self.root.exists()

    def list_skills(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        skills: list[dict[str, Any]] = []
        for md in self.root.rglob("*"):
            if md.is_file() and md.name.lower() == "skill.md":
                try:
                    text = md.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                fm = _frontmatter(text)
                skill_dir = md.parent
                try:
                    rel = skill_dir.relative_to(self.root).parts
                except ValueError:
                    rel = (skill_dir.name,)
                category = rel[0] if len(rel) > 1 else "general"
                skills.append({
                    "name": fm.get("name") or skill_dir.name,
                    "category": fm.get("category") or category,
                    "description": fm.get("description") or _first_paragraph(text),
                    "path": str(skill_dir.relative_to(self.root)) if skill_dir != self.root else ".",
                })
        skills.sort(key=lambda s: (s["category"], s["name"]))
        return skills
