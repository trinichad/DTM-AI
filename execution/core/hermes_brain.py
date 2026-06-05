"""Swap Hermes' brain between the CLOUD model (Codex / gpt-5.5) and the on-box LOCAL model
(Ollama qwen via its OpenAI-compatible /v1) by rewriting the `model:` block in Hermes'
config.yaml.

Why this works (verified): Hermes' api_server resolves its model from config.yaml *per request*
(`_create_agent → _load_gateway_config`), so the swap takes effect on the next turn with no
container restart. The Codex OAuth token lives in a separate `auth.json` that we never touch, so
flipping to local and back never re-authenticates gpt.

This is a GLOBAL setting (one config), not per-chat: switching to local routes EVERY Hermes chat
on-box until switched back. Owner-gated + audited at the API layer.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .config import Config, get_config

# Matches the top-level `model:` block (the `model:` line + its indented body).
_MODEL_RE = re.compile(r"(?m)^model:\n(?:[ \t]+.*\n)*")


def _config_path(cfg: Config) -> Path:
    skills = cfg.get("DTM_HERMES_SKILLS_DIR")
    data_dir = (cfg.get("DTM_HERMES_DATA_DIR")
                or (str(Path(skills).parent) if skills else None)
                or str(Path.home() / ".hermes"))
    return Path(data_dir) / "config.yaml"


def _blocks(cfg: Config) -> dict[str, str]:
    """The two `model:` blocks. Names/URLs are env-overridable so this isn't hard-pinned."""
    cm = cfg.get("HERMES_CLOUD_MODEL") or "gpt-5.5"
    cp = cfg.get("HERMES_CLOUD_PROVIDER") or "openai-codex"
    cb = cfg.get("HERMES_CLOUD_BASE_URL") or "https://chatgpt.com/backend-api/codex"
    lm = cfg.get("HERMES_LOCAL_MODEL") or "qwen3.5:27b"
    lb = cfg.get("HERMES_LOCAL_BASE_URL") or "http://127.0.0.1:11434/v1"
    return {
        "cloud": f"model:\n  default: {cm}\n  provider: {cp}\n  base_url: {cb}\n",
        "local": f"model:\n  default: {lm}\n  provider: custom\n  base_url: {lb}\n",
    }


def _field(block: str, key: str) -> str:
    for line in block.splitlines():
        s = line.strip()
        if s.startswith(key + ":"):
            return s.split(":", 1)[1].strip()
    return ""


def get_brain_mode(cfg: Optional[Config] = None) -> dict:
    """Report Hermes' current brain: {available, mode: cloud|local, model, provider}."""
    cfg = cfg or get_config()
    try:
        text = _config_path(cfg).read_text(encoding="utf-8")
    except OSError:
        return {"available": False, "mode": None, "model": None, "provider": None}
    m = _MODEL_RE.search(text)
    block = m.group(0) if m else ""
    provider = _field(block, "provider")
    model = _field(block, "default")
    mode = "local" if provider == "custom" else "cloud"
    return {"available": bool(block), "mode": mode, "model": model, "provider": provider}


def set_brain_mode(mode: str, cfg: Optional[Config] = None) -> dict:
    """Rewrite the `model:` block to cloud or local. Atomic replace. Returns the new state.
    Raises ValueError on a bad mode, OSError if the config dir isn't writable."""
    if mode not in ("cloud", "local"):
        raise ValueError("mode must be 'cloud' or 'local'")
    cfg = cfg or get_config()
    path = _config_path(cfg)
    text = path.read_text(encoding="utf-8")
    block = _blocks(cfg)[mode]
    new, n = _MODEL_RE.subn(block, text, count=1)
    if n == 0:                                   # no model block yet → prepend one
        new = block + "\n" + text
    tmp = path.with_suffix(".yaml.swaptmp")
    tmp.write_text(new, encoding="utf-8")
    tmp.replace(path)                            # atomic on POSIX
    return get_brain_mode(cfg)
