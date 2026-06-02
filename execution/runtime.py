"""Runtime wiring — builds a ready-to-use Agent with the real registry, audit store,
router, and (later) credentialed client factory. One place to assemble the system so
the CLI and the (future) FastAPI app share identical wiring.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .agent import Agent
from .core.audit import AuditStore
from .core.config import Config, get_config
from .core.context import ToolContext
from .core.dispatch import DenyAllApprovals
from .core.registry import Registry
from .core.router import ModelRouter


def build_agent(cfg: Optional[Config] = None, db_path: Optional[Path] = None) -> Agent:
    cfg = cfg or get_config()
    registry = Registry()                       # discovers execution.skills
    audit = AuditStore(db_path)                  # sqlite dev / (porting target: postgres prod)
    router = ModelRouter(cfg)
    # v1 is read-only by construction: DenyAllApprovals blocks every write tool.
    return Agent(registry, audit, router, gate=DenyAllApprovals())


def make_context(tenant_id: str, actor: str, *, allow_cloud: bool = False) -> ToolContext:
    # client_factory is wired in Phase 3 (clients/); core tools (system_health) don't need it.
    return ToolContext(tenant_id=tenant_id, actor=actor, allow_cloud=allow_cloud)
