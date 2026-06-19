"""Skills registry (Invariant I-1) — auto-discovery, ported from Kaseya Link.

Drop a module into execution/skills/ exporting NAME / DESCRIPTION / PARAMETERS / run
and it becomes a live capability. A module missing any required attribute is silently
skipped (so a half-written tool can't crash the registry).

Hardening over the original:
  - CATEGORY is a real, enforced enum (dispatch reads it).
  - RISK_LEVEL / REQUIRES_APPROVAL are first-class metadata.
  - run() takes (ctx, **kwargs) — the ToolContext security envelope, not a bare client.

Stdlib-only. Discovery uses importlib/pkgutil. No execution happens at discovery time.
"""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Callable, Optional

VALID_CATEGORIES = ("read", "alert", "write", "destructive")
VALID_RISK = ("none", "low", "medium", "high")
_REQUIRED_ATTRS = ("NAME", "DESCRIPTION", "PARAMETERS", "run")


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    parameters: dict[str, Any]
    category: str
    risk_level: str
    requires_approval: bool
    enabled_by_default: bool
    source: str               # integration this tool reads from, for result citations
    run: Callable[..., Any]
    module: str
    group: str = ""           # optional sub-group/family WITHIN a source (D-71), e.g.
                              # "kaseya_command" clusters the run-command toolkit together

    def to_schema(self) -> dict[str, Any]:
        """OpenAI/Ollama function-call wire shape (what the model sees)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @property
    def is_write(self) -> bool:
        return self.category in ("write", "destructive")


def _coerce(module: ModuleType) -> Optional[ToolInfo]:
    if not all(hasattr(module, a) for a in _REQUIRED_ATTRS):
        return None
    category = str(getattr(module, "CATEGORY", "read")).lower()
    if category not in VALID_CATEGORIES:
        category = "read"
    risk = str(getattr(module, "RISK_LEVEL", "low")).lower()
    if risk not in VALID_RISK:
        risk = "low"
    # write/destructive tools require approval unless a module explicitly says otherwise.
    requires_approval = bool(
        getattr(module, "REQUIRES_APPROVAL", category in ("write", "destructive"))
    )
    return ToolInfo(
        name=str(module.NAME),
        description=str(module.DESCRIPTION),
        parameters=dict(module.PARAMETERS),
        category=category,
        risk_level=risk,
        requires_approval=requires_approval,
        enabled_by_default=bool(getattr(module, "ENABLED_BY_DEFAULT", category == "read")),
        source=str(getattr(module, "SOURCE", str(module.NAME).split("_", 1)[0])),
        run=module.run,  # type: ignore[arg-type]
        module=module.__name__,
        group=str(getattr(module, "GROUP", "") or ""),
    )


class Registry:
    def __init__(self, package: str = "execution.skills") -> None:
        self._package = package
        self._tools: dict[str, ToolInfo] = {}
        self.discover()

    def discover(self) -> None:
        """(Re)scan the skills package. Safe to call repeatedly."""
        self._tools = {}
        pkg = importlib.import_module(self._package)
        for mod in pkgutil.iter_modules(pkg.__path__):
            if mod.name.startswith("_"):
                continue
            module = importlib.import_module(f"{self._package}.{mod.name}")
            info = _coerce(module)
            if info is None:
                continue
            if info.name in self._tools:
                raise ValueError(
                    f"duplicate tool NAME '{info.name}' "
                    f"({info.module} vs {self._tools[info.name].module})"
                )
            self._tools[info.name] = info

    def all(self) -> list[ToolInfo]:
        return sorted(self._tools.values(), key=lambda t: t.name)

    def get(self, name: str) -> Optional[ToolInfo]:
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
