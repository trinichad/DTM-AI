"""Read/edit the DTM AI specialist agent team. Each agent is a Hermes profile on the shared
volume: AtlasOps Manager is the `default` profile (the active one chat flows through); the
specialists live under `profiles/<name>/`. Surfaces each agent's SOUL, role, kanban description,
brain (cloud/local), and how it has "compounded" — memory entries, skills, session count — for
the dashboard Agents tab. Edits write the profile's SOUL.md (loaded fresh by Hermes per message).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .config import Config, get_config
from .hermes_brain import _MODEL_RE

_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


def _safe(name: str) -> str:
    if name != "default" and not _NAME_RE.match(name or ""):
        raise ValueError(f"invalid agent name: {name!r}")
    return name


def _data_dir(cfg: Config) -> Path:
    skills = cfg.get("DTM_HERMES_SKILLS_DIR")
    return Path(cfg.get("DTM_HERMES_DATA_DIR")
                or (str(Path(skills).parent) if skills else str(Path.home() / ".hermes")))


def _profile_dir(cfg: Config, name: str) -> Path:
    d = _data_dir(cfg)
    return d if name == "default" else d / "profiles" / _safe(name)


def _soul_field(soul: str, key: str) -> str:
    m = re.search(rf"(?mi)^[-*\s]*{key}:\s*(.+)$", soul)
    return m.group(1).strip() if m else ""


def _count_dir_files(p: Path) -> int:
    try:
        return sum(1 for f in p.iterdir() if f.is_file() and not f.name.startswith("."))
    except OSError:
        return 0


def _count_skills(p: Path) -> int:
    try:
        return sum(1 for f in p.rglob("*") if f.is_file() and f.name.lower() == "skill.md")
    except OSError:
        return 0


def _memory_entries(pd: Path) -> int:
    """Rough 'how much it's learned' count: non-empty, non-heading lines in MEMORY.md."""
    try:
        text = (pd / "MEMORY.md").read_text(encoding="utf-8")
    except OSError:
        return 0
    return sum(1 for l in text.splitlines()
               if l.strip() and not l.lstrip().startswith(("#", "<!--", "-->")))


def _brain(cfg_path: Path) -> dict:
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except OSError:
        return {"mode": None, "model": None}
    m = _MODEL_RE.search(text)
    block = m.group(0) if m else ""
    prov = next((l.split(":", 1)[1].strip() for l in block.splitlines()
                 if l.strip().startswith("provider:")), "")
    model = next((l.split(":", 1)[1].strip() for l in block.splitlines()
                  if l.strip().startswith("default:")), "")
    return {"mode": "local" if prov == "custom" else "cloud", "model": model}


def _read_one(cfg: Config, name: str) -> dict:
    pd = _profile_dir(cfg, name)
    try:
        soul = (pd / "SOUL.md").read_text(encoding="utf-8")
    except OSError:
        soul = ""
    descr = ""
    try:
        for line in (pd / "profile.yaml").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("description:"):
                descr = line.split(":", 1)[1].strip().strip("'\"")
                break
    except OSError:
        pass
    return {
        "id": name,
        "name": _soul_field(soul, "name") or name.replace("_", " ").title(),
        "role": _soul_field(soul, "role"),
        "description": descr,
        "is_manager": name == "default",
        "brain": _brain(pd / "config.yaml"),
        "skills": _count_skills(pd / "skills"),
        "memories": _memory_entries(pd),
        "sessions": _count_dir_files(pd / "sessions"),
        "soul_present": bool(soul.strip()),
    }


def read_memory(name: str, cfg: Optional[Config] = None) -> Optional[dict]:
    """An agent's built-in long-term memory: MEMORY.md (facts it saved) + USER.md (about the team)."""
    cfg = cfg or get_config()
    pd = _profile_dir(cfg, _safe(name))
    if not pd.is_dir():
        return None

    def _read(fn: str) -> str:
        try:
            return (pd / fn).read_text(encoding="utf-8")
        except OSError:
            return ""
    return {"id": name, "memory": _read("MEMORY.md"), "user": _read("USER.md")}


def list_agents(cfg: Optional[Config] = None) -> list[dict]:
    cfg = cfg or get_config()
    out = [_read_one(cfg, "default")]                 # manager first
    pdir = _data_dir(cfg) / "profiles"
    if pdir.is_dir():
        for sub in sorted(p.name for p in pdir.iterdir() if p.is_dir()):
            out.append(_read_one(cfg, sub))
    return out


def get_agent(name: str, cfg: Optional[Config] = None) -> Optional[dict]:
    cfg = cfg or get_config()
    pd = _profile_dir(cfg, _safe(name))
    if not pd.is_dir():
        return None
    a = _read_one(cfg, name)
    try:
        a["soul"] = (pd / "SOUL.md").read_text(encoding="utf-8")
    except OSError:
        a["soul"] = ""
    return a


def set_soul(name: str, text: str, cfg: Optional[Config] = None) -> dict:
    cfg = cfg or get_config()
    pd = _profile_dir(cfg, _safe(name))
    if not pd.is_dir():
        raise FileNotFoundError(f"unknown agent '{name}'")
    (pd / "SOUL.md").write_text(text, encoding="utf-8")   # loaded fresh by Hermes next message
    return get_agent(name, cfg)
