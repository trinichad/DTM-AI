"""Read/edit the DTM AI specialist agent team — native profiles, no external runtime.

Each agent is a profile on disk under the DTM AI agents dir: the AtlasOps Manager is the `default`
profile (the one chat flows through by default); specialists live under `profiles/<name>/`. A
profile is a folder of human-editable markdown + yaml — SOUL.md (persona/role), profile.yaml
(routing description), config.yaml (preferred model), memories/, sessions/, skills/ — so the team
is git-trackable, backup-able, and editable by hand or in the dashboard Agents tab.

Profiles resolve from (in order): DTM_AGENTS_DIR, the legacy DTM_HERMES_DATA_DIR /
DTM_HERMES_SKILLS_DIR (so an existing deployment keeps reading its current profiles until migrated),
else <vault>/agents (DTM_VAULT_PATH or <project>/vault). Edits write the profile's SOUL.md; the
agent loop loads it fresh on the next turn.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

from .config import Config, get_config

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Matches the top-level `model:` block in a profile's config.yaml (the `model:` line + indented body).
_MODEL_RE = re.compile(r"(?m)^model:\n(?:[ \t]+.*\n)*")

_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


def _safe(name: str) -> str:
    if name != "default" and not _NAME_RE.match(name or ""):
        raise ValueError(f"invalid agent name: {name!r}")
    return name


def _data_dir(cfg: Config) -> Path:
    """The agents dir holding the manager (at root) + specialists (under profiles/).
    DTM_AGENTS_DIR wins; then legacy Hermes keys (DTM_HERMES_DATA_DIR, or the parent of
    DTM_HERMES_SKILLS_DIR) so a not-yet-migrated deployment keeps reading its profiles;
    else <vault>/agents (DTM_VAULT_PATH or <project>/vault)."""
    explicit = cfg.get("DTM_AGENTS_DIR") or cfg.get("DTM_HERMES_DATA_DIR")
    if explicit:
        return Path(explicit)
    skills = cfg.get("DTM_HERMES_SKILLS_DIR")          # legacy: infer the data root from skills/
    if skills:
        return Path(skills).parent
    vault = cfg.get("DTM_VAULT_PATH") or str(_PROJECT_ROOT / "vault")
    return Path(vault) / "agents"


def _profile_dir(cfg: Config, name: str) -> Path:
    d = _data_dir(cfg)
    return d if name == "default" else d / "profiles" / _safe(name)


def _yaml_unquote(val: str) -> str:
    """Decode a YAML scalar: single-quoted ('' → '), double-quoted, or bare."""
    if len(val) >= 2 and val[0] == val[-1] == "'":
        return val[1:-1].replace("''", "'")
    if len(val) >= 2 and val[0] == val[-1] == '"':
        return val[1:-1]
    return val


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
                descr = _yaml_unquote(line.split(":", 1)[1].strip())
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
    (pd / "SOUL.md").write_text(text, encoding="utf-8")   # the agent loop loads it fresh next turn
    if name != "default":                 # a specialist's role/name may have changed → refresh roster
        _sync_safe(cfg)
    return get_agent(name, cfg)


def _yaml_str(s: str) -> str:
    """Single-quote a scalar for profile.yaml (YAML escapes ' by doubling it)."""
    return "'" + (s or "").replace("'", "''") + "'"


# ── manager roster — keep AtlasOps' "Team I delegate to" list synced to the live profiles ─────────
_ROSTER_BEGIN = "<!-- TEAM:AUTO — maintained by DTM AI; do not hand-edit between these markers -->"
_ROSTER_END = "<!-- /TEAM:AUTO -->"
_ROSTER_RE = re.compile(r"<!-- TEAM:AUTO.*?<!-- /TEAM:AUTO -->", re.S)
# the hardcoded "## Team I delegate to" section (heading + body up to the next ## or EOF) — migrated once
_ROSTER_SECTION_RE = re.compile(r"(?ms)^## Team I delegate to[^\n]*\n.*?(?=^## |\Z)")


def _roster_block(cfg: Config) -> str:
    """The marker-wrapped roster lines built from the live specialist profiles (manager excluded)."""
    lines = []
    for a in list_agents(cfg):
        if a["is_manager"]:
            continue
        bits = [a["name"]]
        if a.get("role"):
            bits.append(a["role"])
        d = (a.get("description") or "").strip()
        if d:
            bits.append(d[:120])
        lines.append("- " + " — ".join(bits))
    body = "\n".join(lines) if lines else "- (no specialists yet — add one in the Agents tab)"
    return f"{_ROSTER_BEGIN}\n{body}\n{_ROSTER_END}"


def sync_manager_roster(cfg: Optional[Config] = None) -> Optional[dict]:
    """Rewrite the auto-maintained team roster inside AtlasOps' (default) SOUL from the live profiles
    so the manager always knows the real team it can delegate to. Replaces the marker block in place
    if present; otherwise migrates the hardcoded "## Team I delegate to" section; otherwise appends one.
    No-op (returns None) if there's no manager SOUL.
    """
    cfg = cfg or get_config()
    md = _profile_dir(cfg, "default") / "SOUL.md"
    try:
        soul = md.read_text(encoding="utf-8")
    except OSError:
        return None
    block = _roster_block(cfg)
    if _ROSTER_RE.search(soul):
        new = _ROSTER_RE.sub(lambda _m: block, soul)                       # swap block in place
    elif _ROSTER_SECTION_RE.search(soul):
        new = _ROSTER_SECTION_RE.sub(lambda _m: f"## Team I delegate to\n{block}\n\n", soul, count=1)
    else:
        new = soul.rstrip() + f"\n\n## Team I delegate to\n{block}\n"
    if new != soul:
        md.write_text(new, encoding="utf-8")
    n = sum(1 for a in list_agents(cfg) if not a["is_manager"])
    return {"ok": True, "count": n}


def _sync_safe(cfg: Config) -> None:
    """Best-effort roster sync — never let a sync failure break the primary add/delete/edit op."""
    try:
        sync_manager_roster(cfg)
    except OSError:
        pass


def _default_soul(name: str, role: str) -> str:
    """A minimal SOUL stub when the owner doesn't paste one — keeps the fence/honesty rules."""
    title = name.replace("_", " ").replace("-", " ").title()
    lines = [f"# {title}", "", "## Identity", f"- name: {title}"]
    if role:
        lines.append(f"- role: {role}")
    lines += [
        "",
        "## Operating environment",
        "- You are a DTM AI specialist agent. Reach client systems ONLY through the registered",
        "  DTM AI tools (the dispatch fence) — never free-form shell, never invent identifiers or facts.",
        "- Read-only by default. If a tool didn't return it, say you don't know and cite your sources.",
        "",
    ]
    return "\n".join(lines)


def create_agent(name: str, soul: str = "", description: str = "", role: str = "",
                 cfg: Optional[Config] = None) -> dict:
    """Add a new specialist agent = a fresh profile on disk under `profiles/<name>/`.

    Profiles are discovered by scanning that directory, so writing the files IS the create. The new
    profile inherits the manager's config.yaml (preferred model) so it answers immediately through
    the same DTM AI tools; its model can be changed per-agent afterward.
    """
    cfg = cfg or get_config()
    name = _safe(name)
    if name == "default":
        raise ValueError("'default' is reserved for the AtlasOps manager")
    pd = _profile_dir(cfg, name)
    if pd.exists():
        raise FileExistsError(f"agent '{name}' already exists")

    data = _data_dir(cfg)
    pd.mkdir(parents=True, exist_ok=False)
    for sub in ("memories", "sessions", "skills"):
        (pd / sub).mkdir(exist_ok=True)

    src_cfg = data / "config.yaml"          # inherit the manager's brain + tool config
    if src_cfg.is_file():
        shutil.copyfile(src_cfg, pd / "config.yaml")

    soul = soul if (soul and soul.strip()) else _default_soul(name, role)
    (pd / "SOUL.md").write_text(soul, encoding="utf-8")
    descr = " ".join((description or "").split())
    (pd / "profile.yaml").write_text(
        f"description: {_yaml_str(descr)}\ndescription_auto: false\n", encoding="utf-8")
    _sync_safe(cfg)                        # make AtlasOps aware of the new specialist
    return get_agent(name, cfg)


def delete_agent(name: str, cfg: Optional[Config] = None) -> dict:
    """Remove a specialist agent (its whole profile dir). The manager (`default`) is protected.

    Best-effort cleanup of any per-profile alias + gateway logs a prior runtime may have created so a
    deleted agent leaves nothing behind.
    """
    cfg = cfg or get_config()
    name = _safe(name)
    if name == "default":
        raise ValueError("the AtlasOps manager (default) cannot be deleted")
    pd = _profile_dir(cfg, name)
    if not pd.is_dir():
        raise FileNotFoundError(f"unknown agent '{name}'")
    shutil.rmtree(pd)

    data = _data_dir(cfg)
    for extra in (data / ".local" / "bin" / name, data / "logs" / "gateways" / name):
        try:
            if extra.is_dir():
                shutil.rmtree(extra)
            elif extra.exists():
                extra.unlink()
        except OSError:
            pass
    _sync_safe(cfg)                        # drop the deleted agent from AtlasOps' roster
    return {"id": name, "deleted": True}
