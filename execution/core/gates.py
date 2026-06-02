"""Approval gates — the policy objects dispatch() consults for write/destructive tools.

DenyAllApprovals        hard read-only (tests / ultra-safe default).
ConfigurableApprovalGate reads the Capability Console policy AND, in production, DEFERS every
                        approval-needed write to the explicit human approval workflow (it never
                        runs an approval-needed write inline). Trusted (require_approval=False,
                        non-destructive) writes run immediately.
AlwaysApprove           used by the API to EXECUTE an already-approved action exactly once.
"""
from __future__ import annotations

from typing import Optional

from .capabilities import CapabilityStore
from .registry import Registry


class ConfigurableApprovalGate:
    def __init__(self, caps: CapabilityStore, registry: Registry, approvals=None) -> None:
        self.caps = caps
        self.registry = registry
        self.approvals = approvals   # ApprovalStore in production; None in unit tests

    def _policy(self, tool: str):
        info = self.registry.get(tool)
        default_enabled = bool(info and info.enabled_by_default)
        return self.caps.get(tool, default_enabled=default_enabled), info

    def write_allowed_for_tenant(self, tenant_id: str, tool: str) -> bool:
        policy, _info = self._policy(tool)
        return policy.allow_write

    def needs_approval(self, tenant_id: str, tool: str) -> bool:
        """Destructive ALWAYS needs approval (floor); otherwise the policy decides."""
        policy, info = self._policy(tool)
        return bool(info and info.category == "destructive") or policy.require_approval

    def consume(self, token: Optional[str], tenant_id: str, tool: str, args: dict) -> bool:
        """Return True iff the action may run RIGHT NOW.

        - approval not needed (trusted, non-destructive)        -> run now
        - approval needed + an ApprovalStore is wired (prod)    -> NEVER run inline; defer to the
                                                                   human approval workflow
        - approval needed + no store (tests)                    -> a present token counts as approval
        """
        if not self.needs_approval(tenant_id, tool):
            return True
        if self.approvals is not None:
            return False
        return bool(token)


class AlwaysApprove:
    """Run an already-approved action once (used only by the approval-execution path)."""
    def write_allowed_for_tenant(self, tenant_id: str, tool: str) -> bool:
        return True

    def needs_approval(self, tenant_id: str, tool: str) -> bool:
        return False

    def consume(self, token: Optional[str], tenant_id: str, tool: str, args: dict) -> bool:
        return True
