"""ToolContext — the per-call security envelope passed to every tool's run(ctx, ...).

Replaces Kaseya Link's bare `kaseya` positional arg. Carries the tenant the call is
bound to, the human actor, and a *scoped* client factory so a tool can only ever
construct clients for its own tenant (Behavioral Rule #4 — tenant isolation).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class CrossTenantError(PermissionError):
    """Raised when a tool tries to reach outside the tenant it is bound to."""


@dataclass
class ToolContext:
    tenant_id: str                       # the client this call is bound to ("*" = all, read-only views)
    actor: str                           # human/user identity, for the audit log
    allow_cloud: bool = False            # may this task leave the local LLM? (Rule #5)
    # client_factory(integration, tenant_id) -> a constructed, credentialed vendor client.
    # Injected by the runtime; defaults to None so the core unit-tests need no creds.
    client_factory: Optional[Callable[[str, str], Any]] = None
    _meta: dict[str, Any] = field(default_factory=dict)
    # Latest batch progress for the live UI heartbeat (D-112). A long single tool call (e.g. a
    # 52-item batch) otherwise streams nothing but elapsed time, so it reads as frozen. A tool
    # updates this via progress(); the agent's _dispatch_heartbeat reads it each tick and streams it.
    _progress: Optional[dict] = None

    def progress(self, done: int, total: int = 0, label: str = "") -> None:
        """Report batch progress for the live UI ('23/52 — KSiza@…'). Safe no-op off-stream."""
        try:
            self._progress = {"done": int(done), "total": int(total), "label": str(label)[:80]}
        except Exception:                            # progress is cosmetic — never break a tool
            pass

    def map_progress(self, items, fn, label=None):
        """Run fn over items, reporting progress to the heartbeat after each one. `label(item)->str`
        names the current item for the UI (defaults to str(item)). Returns the list of results —
        a drop-in for `[fn(x) for x in items]` that makes a batch tool's progress visible live."""
        out, n = [], len(items)
        for i, it in enumerate(items):
            self.progress(i, n, (label(it) if label else str(it)))
            out.append(fn(it))
        self.progress(n, n, "")
        return out

    def client(self, integration: str) -> Any:
        """Get a vendor client for THIS tenant only. Fail-closed if no factory wired."""
        if self.client_factory is None:
            raise RuntimeError(
                f"no client_factory wired into ToolContext; cannot build '{integration}' client"
            )
        return self.client_factory(integration, self.tenant_id)

    def require_tenant(self, tenant_id: str) -> None:
        """Guard: a tool must not act on a tenant other than the one it is bound to."""
        if self.tenant_id == "*":
            return  # cross-client read view; individual tools still scope their reads
        if tenant_id != self.tenant_id:
            raise CrossTenantError(
                f"call bound to tenant '{self.tenant_id}' may not act on '{tenant_id}'"
            )
