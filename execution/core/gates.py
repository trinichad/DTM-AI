"""Approval gates — the policy objects dispatch() consults for write/destructive tools.

DenyAllApprovals        hard read-only (tests / ultra-safe default).
ConfigurableApprovalGate reads the Capability Console policy AND, in production, DEFERS every
                        approval-needed write to the explicit human approval workflow (it never
                        runs an approval-needed write inline). Trusted (require_approval=False,
                        non-destructive) writes run immediately.
AlwaysApprove           used by the API to EXECUTE an already-approved action exactly once.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

from .capabilities import CapabilityStore
from .registry import Registry


class ConfigurableApprovalGate:
    # Batch grants (D-59): "approve once, auto-approve the repeats" — bounded, never blanket.
    BATCH_TTL_S = 900          # a grant lives 15 minutes
    BATCH_DEFAULT = 25         # default repeats per grant
    BATCH_MAX = 200            # hard cap, whatever the request asked for

    def __init__(self, caps: CapabilityStore, registry: Registry, approvals=None) -> None:
        self.caps = caps
        self.registry = registry
        self.approvals = approvals   # ApprovalStore in production; None in unit tests
        self._batch: dict[tuple[str, str], dict] = {}   # (tenant, tool) → grant
        self._batch_lock = threading.Lock()
        self._tls = threading.local()

    def _policy(self, tool: str):
        info = self.registry.get(tool)
        default_enabled = bool(info and info.enabled_by_default)
        return self.caps.get(tool, default_enabled=default_enabled), info

    @staticmethod
    def _is_own_vault_write(info) -> bool:
        """MSP AI's OWN tools (source=msp_ai) that write only touch our markdown vault/memory —
        never a client system. They are safe by construction (D-47): always allowed, never
        approval-gated, regardless of any stale capability row. The owner's lever for these is the
        enable/kill switch (checked in dispatch), not allow_write/approval."""
        return bool(info and info.source == "msp_ai" and info.category == "write")

    def write_allowed_for_tenant(self, tenant_id: str, tool: str) -> bool:
        policy, info = self._policy(tool)
        return self._is_own_vault_write(info) or policy.allow_write

    def needs_approval(self, tenant_id: str, tool: str) -> bool:
        """Destructive ALWAYS needs approval (floor); own-vault writes NEVER do; else the policy
        decides (the per-tool 'Approval' toggle — off ⇒ the trusted write auto-runs)."""
        policy, info = self._policy(tool)
        if self._is_own_vault_write(info):
            return False
        return bool(info and info.category == "destructive") or policy.require_approval

    def consume(self, token: Optional[str], tenant_id: str, tool: str, args: dict) -> bool:
        """Return True iff the action may run RIGHT NOW.

        - approval not needed (trusted, non-destructive)        -> run now
        - a live batch grant covers (tenant, tool) (D-59)       -> run now, decrement the grant
        - approval needed + an ApprovalStore is wired (prod)    -> NEVER run inline; defer to the
                                                                   human approval workflow
        - approval needed + no store (tests)                    -> a present token counts as approval
        """
        if not self.needs_approval(tenant_id, tool):
            return True
        g = self._take_batch(tenant_id, tool)
        if g is not None:
            self._tls.batch_note = (f"auto-approved by batch grant (approval#{g['approval_id']}, "
                                    f"{g['remaining']} of {g['granted']} left)")
            return True
        if self.approvals is not None:
            return False
        return bool(token)

    # ── batch grants (D-59) — approve once, auto-approve the next N identical calls ──────────
    def grant_batch(self, tenant_id: str, tool: str, *, count: Optional[int] = None,
                    approval_id: int = 0, by: str = "") -> Optional[dict[str, Any]]:
        """Arm a grant: the next `count` calls of `tool` for `tenant_id` auto-approve (TTL'd).
        Returns the grant, or None when refused. FLOOR: destructive tools can never be
        batch-granted (Rule #1 — per-action approval always)."""
        info = self.registry.get(tool)
        if not info or info.category == "destructive":
            return None
        n = max(1, min(int(count or self.BATCH_DEFAULT), self.BATCH_MAX))
        g = {"tenant_id": tenant_id, "tool": tool, "granted": n, "remaining": n,
             "expires_at": time.time() + self.BATCH_TTL_S,
             "approval_id": int(approval_id), "by": by}
        with self._batch_lock:
            self._batch[(tenant_id, tool)] = g
        return dict(g)

    def _take_batch(self, tenant_id: str, tool: str) -> Optional[dict[str, Any]]:
        """Consume one repeat from a live grant — or None. Re-checks the destructive floor."""
        info = self.registry.get(tool)
        if not info or info.category == "destructive":
            return None
        with self._batch_lock:
            g = self._batch.get((tenant_id, tool))
            if not g:
                return None
            if g["expires_at"] <= time.time() or g["remaining"] <= 0:
                self._batch.pop((tenant_id, tool), None)
                return None
            g["remaining"] -= 1
            return dict(g)

    def list_batches(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._batch_lock:
            self._batch = {k: v for k, v in self._batch.items()
                           if v["remaining"] > 0 and v["expires_at"] > now}
            return [{**v, "expires_in_s": int(v["expires_at"] - now)}
                    for v in self._batch.values()]

    def revoke_batches(self, tenant_id: Optional[str] = None,
                       tool: Optional[str] = None) -> int:
        with self._batch_lock:
            keys = [k for k in self._batch
                    if (tenant_id is None or k[0] == tenant_id)
                    and (tool is None or k[1] == tool)]
            for k in keys:
                del self._batch[k]
            return len(keys)

    def pop_batch_note(self) -> Optional[str]:
        """dispatch() collects this right after consume() — the audit detail for an
        auto-approved run (thread-local, so concurrent dispatches can't mislabel)."""
        note = getattr(self._tls, "batch_note", None)
        self._tls.batch_note = None
        return note


class AlwaysApprove:
    """Run an already-approved action once (used only by the approval-execution path)."""
    def write_allowed_for_tenant(self, tenant_id: str, tool: str) -> bool:
        return True

    def needs_approval(self, tenant_id: str, tool: str) -> bool:
        return False

    def consume(self, token: Optional[str], tenant_id: str, tool: str, args: dict) -> bool:
        return True
