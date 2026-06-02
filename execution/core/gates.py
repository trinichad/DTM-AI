"""Approval gates — the policy objects dispatch() consults for write/destructive tools.

DenyAllApprovals       hard read-only (used by tests and as the ultra-safe default).
ConfigurableApprovalGate  reads the Capability Console policy so the owner can open
                       capabilities tool-by-tool, ramping toward autonomy — while the
                       safety floors stay enforced here in code.
"""
from __future__ import annotations

from typing import Optional

from .capabilities import CapabilityStore
from .registry import Registry


class ConfigurableApprovalGate:
    def __init__(self, caps: CapabilityStore, registry: Registry) -> None:
        self.caps = caps
        self.registry = registry

    def _policy(self, tool: str):
        info = self.registry.get(tool)
        default_enabled = bool(info and info.enabled_by_default)
        return self.caps.get(tool, default_enabled=default_enabled), info

    def write_allowed_for_tenant(self, tenant_id: str, tool: str) -> bool:
        policy, _info = self._policy(tool)
        return policy.allow_write

    def consume(self, token: Optional[str], tenant_id: str, tool: str, args: dict) -> bool:
        policy, info = self._policy(tool)
        # SAFETY FLOOR: destructive tools ALWAYS require an approval token, even if the
        # owner set require_approval=False. Autonomy is allowed to ramp on writes, never
        # on destructive actions, without an explicit per-action approval.
        force_approval = bool(info and info.category == "destructive")
        if policy.require_approval or force_approval:
            # Phase-2 will mint real one-shot, args-bound tokens. For now a present token
            # represents a human approval; absence blocks the action (fail-closed).
            return bool(token)
        # Owner has explicitly marked this (write, non-destructive) tool trusted for
        # autonomous use — it runs without a per-action approval. Still audited.
        return True
