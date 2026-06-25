"""dispatch() — the single chokepoint every tool call passes through.

This is where the constitution's Behavioral Rules stop being prose and become code
(the #1 gap found in the Kaseya Link build). In order, dispatch:

  1. resolves the tool (unknown -> refuse + audit)
  2. checks the enable flag (Invariant I-4 kill switch; disabled -> refuse even if the
     model named it)
  3. validates args against PARAMETERS (Rule #3; bad args -> refuse, no run())
  4. enforces CATEGORY (Rule #1): read/alert run; write/destructive are BLOCKED unless
     the ApprovalGate grants a one-shot token AND the tenant's write flag is on
  5. runs the tool inside the ToolContext (tenant-scoped), catching all exceptions
  6. ALWAYS writes an audit record
  7. returns the uniform result envelope (§2.3)

A tool can never run as a side effect of being *named* — only by passing every gate.
"""
from __future__ import annotations

import time
from typing import Any, Optional, Protocol

from .audit import AuditStore
from .context import CrossTenantError, ToolContext
from .registry import Registry, ToolInfo
from .validation import SchemaError, validate_args


class ApprovalGate(Protocol):
    """Pluggable approval policy. v1 default denies all writes (read-only platform)."""

    def write_allowed_for_tenant(self, tenant_id: str, tool: str) -> bool: ...
    def consume(self, token: Optional[str], tenant_id: str, tool: str, args: dict) -> bool: ...


class DenyAllApprovals:
    """Default gate: no writes, ever. This is what makes v1 read-only by construction."""

    def write_allowed_for_tenant(self, tenant_id: str, tool: str) -> bool:
        return False

    def needs_approval(self, tenant_id: str, tool: str) -> bool:
        return True

    def consume(self, token: Optional[str], tenant_id: str, tool: str, args: dict) -> bool:
        return False


def _envelope(
    ok: bool, source: str, tenant_id: str, data: Any = None,
    error: Optional[str] = None, latency_ms: int = 0,
) -> dict[str, Any]:
    return {
        "ok": ok, "source": source, "tenant_id": tenant_id,
        "data": data, "error": error, "latency_ms": latency_ms,
    }


# How much tool output we let back into the LLM context (bound, ported from the
# original 20000-char cap). Truncation is by length; sensitive-field minimization is
# the tool's job (it should _slim its payload).
MAX_RESULT_CHARS = 20_000

# bulk (D-111) bounds a single fan-out. Far beyond any real MSP request; a runaway backstop.
BULK_MAX_ITEMS = 200


def _run_bulk(*, registry, audit, ctx, gate, approvals, valid, deny):
    """Execute the `bulk` meta-tool: run `valid['tool']` once per `valid['items']` entry, each via
    a fresh dispatch() so every per-item guardrail still fires. Returns ONE aggregated envelope.

    Approval handling reuses the existing flow verbatim: an item that auto-approves (trusted write
    or a live D-59 batch grant) runs inline; an item that needs human sign-off makes dispatch()
    return a pending_approval envelope — bulk surfaces THAT one card and stops, so approvals stay
    one-at-a-time (no orphan pile-up, D-47). Re-invoke bulk after the owner decides to continue;
    already-applied items re-run harmlessly because the underlying tools verify/self-heal."""
    inner = str(valid.get("tool") or "").strip()
    items = valid.get("items") or []
    if inner == "bulk":
        return deny("bulk cannot run 'bulk' (no nesting)")
    info = registry.get(inner)
    if info is None:
        return deny(f"bulk: unknown tool '{inner}'")
    if len(items) > BULK_MAX_ITEMS:
        return deny(f"bulk: {len(items)} items exceeds the {BULK_MAX_ITEMS}-item cap — split it up")
    src = info.source
    results: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        try:
            ctx.progress(i, len(items), inner)        # live heartbeat: "12/52 <tool>" (D-112)
        except Exception:
            pass
        if not isinstance(item, dict):
            results.append({"index": i, "ok": False,
                            "error": "each item must be an object of arguments"})
            continue
        env = dispatch(registry=registry, audit=audit, ctx=ctx, name=inner, args=item,
                       gate=gate, approvals=approvals)
        if env.get("status") == "pending_approval":
            # one card at a time — surface this approval and pause; bulk is re-invoked to continue.
            env = dict(env)
            env["bulk"] = {"tool": inner, "total": len(items), "completed": i,
                           "remaining": len(items) - i, "paused_index": i, "results": results}
            return env
        results.append({"index": i, "ok": bool(env.get("ok")),
                        **({"data": env.get("data")} if env.get("ok")
                           else {"error": env.get("error")})})
    ok_n = sum(1 for r in results if r.get("ok"))
    audit.record(actor=ctx.actor, tenant_id=ctx.tenant_id, action="tool_call", tool="bulk",
                 category="read", args={"tool": inner, "items": len(items)}, result_ok=True,
                 detail=f"bulk {inner} x{len(items)} ({ok_n} ok, {len(results) - ok_n} failed)")
    return _envelope(True, src, ctx.tenant_id,
                     data={"tool": inner, "count": len(results), "ok_count": ok_n,
                           "error_count": len(results) - ok_n, "results": results})


def dispatch(
    *,
    registry: Registry,
    audit: AuditStore,
    ctx: ToolContext,
    name: str,
    args: Optional[dict[str, Any]] = None,
    approval_token: Optional[str] = None,
    gate: Optional[ApprovalGate] = None,
    approvals=None,
) -> dict[str, Any]:
    args = args or {}
    gate = gate or DenyAllApprovals()
    tool: Optional[ToolInfo] = registry.get(name)
    src = tool.source if tool else name

    def deny(reason: str, *, category: Optional[str] = None) -> dict[str, Any]:
        audit.record(
            actor=ctx.actor, tenant_id=ctx.tenant_id, action="tool_denied",
            tool=name, category=category, args=args, result_ok=False, detail=reason,
        )
        return _envelope(False, src, ctx.tenant_id, error=reason)

    # 1. unknown tool
    if tool is None:
        return deny(f"unknown tool '{name}'")

    # 2. kill switch (config, not code)
    if not audit.is_enabled(name, tool.enabled_by_default):
        return deny(f"tool '{name}' is disabled", category=tool.category)

    # 3. validate args BEFORE running anything
    try:
        valid = validate_args(tool.parameters, args)
    except SchemaError as e:
        return deny(f"invalid arguments: {e}", category=tool.category)

    # 3b. bulk meta-tool (D-111): one tool call, many runs. Each item RE-ENTERS dispatch() so it
    # gets the full gate (validation, kill switch, category + approval, tenant isolation, audit) —
    # bulk grants no authority, it only collapses the N-round loop into one call.
    if name == "bulk":
        return _run_bulk(registry=registry, audit=audit, ctx=ctx, gate=gate,
                         approvals=approvals, valid=valid, deny=deny)

    # 4. CATEGORY enforcement — write/destructive gated by capability + approval workflow
    batch_note: Optional[str] = None
    if tool.is_write:
        if not gate.write_allowed_for_tenant(ctx.tenant_id, name):
            return deny(
                f"write tool '{name}' blocked: tenant '{ctx.tenant_id}' has no write flag",
                category=tool.category,
            )
        if gate.consume(approval_token, ctx.tenant_id, name, valid):
            # a batch grant (D-59) may have auto-approved this call — audit says so
            batch_note = getattr(gate, "pop_batch_note", lambda: None)()
        else:
            needs = getattr(gate, "needs_approval", lambda t, n: True)(ctx.tenant_id, name)
            if needs and approvals is not None:
                # A human-readable preview of the proposed write (e.g. group id → "Autopilot
                # users"), resolved here so the owner confirms intent, not a raw GUID. Best-effort
                # + read-only: any failure falls back to showing the raw args (Rule #2 — never
                # invent; this only RESOLVES identifiers the tool already understands).
                preview = None
                if tool.describe_approval is not None:
                    try:
                        preview = tool.describe_approval(ctx, valid)
                    except Exception:  # noqa: BLE001 — preview is cosmetic, never block the approval
                        preview = None
                # Don't execute — record a proposed action for explicit human review.
                aid = approvals.create(actor=ctx.actor, tenant_id=ctx.tenant_id,
                                       tool=name, category=tool.category, args=valid,
                                       conversation_id=ctx._meta.get("conversation_id"),
                                       args_preview=preview)
                audit.record(actor=ctx.actor, tenant_id=ctx.tenant_id,
                             action="approval_requested", tool=name, category=tool.category,
                             args=valid, result_ok=False, detail=f"approval#{aid}")
                env = _envelope(False, src, ctx.tenant_id,
                                error="approval required — submitted for human review")
                env["approval_id"] = aid
                env["status"] = "pending_approval"
                env["approval_preview"] = preview
                return env
            return deny(
                f"write tool '{name}' blocked: missing/invalid approval", category=tool.category,
            )

    # 5. run, tenant-scoped, catching everything (Rule: tools never raise to the loop)
    started = time.monotonic()
    try:
        result = tool.run(ctx, **valid)
    except CrossTenantError as e:
        return deny(f"tenant isolation violation: {e}", category=tool.category)
    except Exception as e:  # noqa: BLE001 — deliberate: surface as data, never crash the loop
        latency = int((time.monotonic() - started) * 1000)
        audit.record(
            actor=ctx.actor, tenant_id=ctx.tenant_id, action="tool_error",
            tool=name, category=tool.category, args=valid, result_ok=False, detail=str(e),
        )
        return _envelope(False, src, ctx.tenant_id, error=f"{type(e).__name__}: {e}", latency_ms=latency)

    latency = int((time.monotonic() - started) * 1000)

    # tools may signal failure with {"error": ...} instead of raising
    if isinstance(result, dict) and "error" in result and result.get("ok") is not True:
        audit.record(
            actor=ctx.actor, tenant_id=ctx.tenant_id, action="tool_call",
            tool=name, category=tool.category, args=valid, result_ok=False,
            detail=str(result.get("error")) + (f" [{batch_note}]" if batch_note else ""),
        )
        env = _envelope(False, src, ctx.tenant_id, error=str(result["error"]), latency_ms=latency)
        if batch_note:
            env["auto_approved"] = batch_note
        return env

    audit.record(
        actor=ctx.actor, tenant_id=ctx.tenant_id, action="tool_call",
        tool=name, category=tool.category, args=valid, result_ok=True,
        detail=batch_note,
    )
    env = _envelope(True, src, ctx.tenant_id, data=result, latency_ms=latency)
    if batch_note:
        env["auto_approved"] = batch_note
    return env
