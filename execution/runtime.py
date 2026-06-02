"""Runtime wiring — builds a ready-to-use Agent with the real registry, audit store,
router, and (later) credentialed client factory. One place to assemble the system so
the CLI and the (future) FastAPI app share identical wiring.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .agent import Agent
from .clients import ClientFactory
from .core.audit import AuditStore
from .core.capabilities import CapabilityStore
from .core.config import Config, get_config
from .core.context import ToolContext
from .core.gates import ConfigurableApprovalGate
from .core.registry import Registry
from .core.router import ModelRouter

# Shared, caching client factory (tokens/rate-limiters live here across calls).
_factory: Optional[ClientFactory] = None


def get_client_factory(cfg: Optional[Config] = None) -> ClientFactory:
    global _factory
    if _factory is None:
        _factory = ClientFactory(cfg or get_config())
    return _factory


def build_agent(cfg: Optional[Config] = None, db_path: Optional[Path] = None) -> Agent:
    cfg = cfg or get_config()
    registry = Registry()                       # discovers execution.skills
    audit = AuditStore(db_path)                  # sqlite dev / (porting target: postgres prod)
    caps = CapabilityStore(db_path)              # the Capability Console's policy store
    router = ModelRouter(cfg)
    # Read-only by DEFAULT (no capability rows -> allow_write False everywhere), but now
    # tunable per tool via the Capability Console as trust is earned. Safety floors live
    # in ConfigurableApprovalGate + dispatch + audit and cannot be toggled off.
    gate = ConfigurableApprovalGate(caps, registry)
    agent = Agent(registry, audit, router, gate=gate)
    agent.caps = caps                            # expose for the console/CLI
    return agent


def make_context(tenant_id: str, actor: str, *, allow_cloud: bool = False) -> ToolContext:
    # Tenant-scoped client factory: a tool gets vendor clients for its bound tenant only.
    return ToolContext(tenant_id=tenant_id, actor=actor, allow_cloud=allow_cloud,
                       client_factory=get_client_factory())
