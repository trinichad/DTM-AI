"""Scoped read connectors — the trusted primitive boundary (D-15).

The AI/Hermes composes unlimited *learned skills*, but only out of these guarded
primitives. A scoped read connector accepts an arbitrary vendor API `path` yet enforces:
  - GET only (the vendor clients' .get() is read-only)
  - the path must match a per-vendor ALLOWLIST of safe read prefixes
  - no host escape (must start with "/", no scheme, no "//", no "..")
  - auth/token endpoints are NOT in the allowlist (handled internally, never exposed)

This is what lets "no hand-made tools — all learned skills" coexist with "security is
the highest priority": skills can compose freely, but the reachable surface is fixed,
read-only, tenant-scoped, and audited. Widening a vendor's reach = adding a prefix here
(reviewed config), never AI-improvised. Writes are SEPARATE, individually-gated primitives.
"""
from __future__ import annotations

from typing import Any, Optional

# Per-vendor allowlist of safe READ path prefixes. Conservative by default; extend
# deliberately. NOTE: auth/token endpoints are intentionally absent.
READ_SCOPES: dict[str, tuple[str, ...]] = {
    "kaseya": (                 # VSA 9.5 REST (/api/v1.0 prefix added by the client)
        "/assetmgmt/",          # assets, agents, audit summaries, hardware/software
        "/system/orgs",         # organizations
        "/system/machinegroups",
        "/audit/",
    ),
    "cylance": (
        "/devices/v2",
        "/threats/v2",
        "/policies/v2",
        "/zones/v2",
        "/users/v2",
        "/detections/v2",
        "/memoryprotection/v2",
    ),
    "huntress": (
        "/account",
        "/agents",
        "/organizations",
        "/incident_reports",
        "/reports",
        "/billing_reports",
    ),
}


def is_allowed_read(vendor: str, path: str) -> tuple[bool, str]:
    """Return (allowed, reason). Fail-closed on anything not provably safe."""
    prefixes = READ_SCOPES.get(vendor)
    if prefixes is None:
        return False, f"unknown vendor '{vendor}'"
    if not isinstance(path, str) or not path.startswith("/"):
        return False, "path must be a string beginning with '/'"
    if "://" in path or path.startswith("//") or ".." in path:
        return False, "path may not contain a scheme, '//', or '..' (no host escape)"
    # boundary-aware prefix match: "/account" matches "/account" and "/account/..",
    # but NOT "/account_settings".
    base = path.split("?", 1)[0]
    if not any(_matches(base, p) for p in prefixes):
        return False, (f"path '{path}' is not in the {vendor} read allowlist "
                       f"(allowed prefixes: {', '.join(prefixes)})")
    return True, "ok"


def _matches(path: str, prefix: str) -> bool:
    p = prefix.rstrip("/")
    return path == p or path.startswith(p + "/")


def scoped_read(ctx, vendor: str, path: str, params: Optional[dict] = None) -> Any:
    """Validate a read path against the allowlist, then call the tenant's vendor client.
    Returns the raw JSON, or {"error": ...} if the path is not allowed (client NOT called)."""
    allowed, reason = is_allowed_read(vendor, path)
    if not allowed:
        return {"error": f"read blocked: {reason}"}
    return ctx.client(vendor).get(path, params or None)
