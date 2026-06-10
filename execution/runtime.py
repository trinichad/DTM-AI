"""Runtime wiring — builds a ready-to-use Agent with the real registry, audit store,
router, and (later) credentialed client factory. One place to assemble the system so
the CLI and the (future) FastAPI app share identical wiring.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .agent import Agent
from .clients import ClientFactory
from .core.approvals import ApprovalStore
from .core.audit import AuditStore
from .core.capabilities import CapabilityStore
from .core.conversations import ConversationStore
from .core.config import Config, get_config
from .core.context import ToolContext
from .core.gates import ConfigurableApprovalGate
from .core.registry import Registry
from .core.router import ModelRouter
from .core.tasks import Dispatcher, TaskStore

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
    approvals = ApprovalStore(db_path)           # write-action approval workflow
    conversations = ConversationStore(db_path)   # per-user persistent chat history (multi-chat)
    tasks = TaskStore(db_path)                    # native delegation board (D-19; replaces kanban)
    router = ModelRouter(cfg)
    # Read-only by DEFAULT (no capability rows -> allow_write False everywhere), but now
    # tunable per tool via the Capability Console as trust is earned. Safety floors live
    # in ConfigurableApprovalGate + dispatch + audit and cannot be toggled off.
    # Seed internal-write policy: DTM AI's OWN tools (source=dtm_ai) that write only touch our
    # vault/memory — not client systems — so they're allowed by default (still shown + toggleable
    # in the Capability Console). Only seed when no policy row exists, so owner changes persist.
    existing = caps.all()
    for t in registry.all():
        if t.source == "dtm_ai" and t.is_write and t.name not in existing:
            caps.set(t.name, allow_write=True, require_approval=t.requires_approval)
    gate = ConfigurableApprovalGate(caps, registry, approvals=approvals)
    agent = Agent(registry, audit, router, gate=gate, cfg=cfg)   # cfg → profile-aware prompts
    agent.caps = caps                            # expose for the console/CLI
    agent.approvals = approvals                  # expose for the approval API + agent dispatch
    agent.conversations = conversations          # expose for the chat-history API
    agent.tasks = tasks                          # expose for the delegation API
    # Delegation worker: runs the agent loop AS the assigned profile, bound to the task's tenant,
    # local-first (allow_cloud=False) per Rule #5. Same guarded loop → every call still audited.
    from .core.agents import get_brain_model
    agent.dispatcher = Dispatcher(
        tasks, agent, lambda tenant, actor: make_context(tenant, actor, allow_cloud=False),
        model_resolver=lambda profile: get_brain_model(profile, cfg))
    # Recurrence tick for scheduled delegation. Constructed here, STARTED only by the long-running
    # server (create_server) — never in build_agent's test/CLI callers.
    from .core.scheduler import Scheduler
    agent.scheduler = Scheduler(tasks, agent.dispatcher)
    return agent


def make_context(tenant_id: str, actor: str, *, allow_cloud: bool = False) -> ToolContext:
    # Tenant-scoped client factory: a tool gets vendor clients for its bound tenant only.
    return ToolContext(tenant_id=tenant_id, actor=actor, allow_cloud=allow_cloud,
                       client_factory=get_client_factory())
