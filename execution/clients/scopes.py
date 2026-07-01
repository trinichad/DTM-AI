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
    "kaseya": (                 # VSA 9.x REST (/api/v1.0 prefix added by the client)
        "/assetmgmt/",          # assets, agents, audit detail (hardware/software/accounts)
        "/system/orgs",         # organizations, departments, locations, staff
        "/system/machinegroups",
        "/automation/",         # agent procedures + service desk (D-68)
        # NB: alarms live at /assetmgmt/alarms/{returnAllRecords} — already covered by /assetmgmt/.
        # There is NO bare /alarms endpoint in VSA 9 (it 404s); don't re-add one.
    ),
    "cylance": (
        "/devices/v2",
        "/threats/v2",
        "/policies/v2",
        "/zones/v2",
        "/users/v2",
        "/detections/v2",
        "/memoryprotection/v2",
        "/globallists/v2",      # global safe/quarantine list contents (D-82)
        "/instaquery/v2",       # Optics InstaQuery results (D-82)
    ),
    "gws": (                    # Google Workspace Admin SDK Directory API (read-only — D-118)
        "/admin/directory/v1/users",           # users + a user's aliases (users/{key}/aliases)
        "/admin/directory/v1/groups",          # groups + members (groups/{key}/members)
        "/admin/directory/v1/customers",       # customer/domain record (probe) — note: plural
        "/admin/directory/v1/customer",        # org units live under customer/{id}/orgunits (singular)
    ),
    "m365": (                   # Microsoft Graph v1.0 (delegated, read-only — D-32)
        "/users",
        "/groups",
        "/organization",
        "/subscribedSkus",
        "/directoryRoles",
        "/devices",
        "/me",
        "/sites",               # SharePoint site listing/details (D-56)
        "/deviceManagement",    # Intune/Autopilot device reads (D-56)
    ),
    "huntress": (
        "/account",
        "/agents",
        "/organizations",
        "/incident_reports",
        "/reports",
        "/summary_reports",     # summary reports (D-82)
        "/billing_reports",
        "/escalations",         # escalations list/detail (D-82)
    ),
    "proofpoint": (             # Proofpoint Essentials API v1 reads (D-86)
        "/orgs",                # /orgs/{domain} + /orgs/{domain}/users, /domains
        "/endpoints",           # which stack an org is on
    ),
    "unifi": (                  # UniFi Network local Integration API reads (D-84)
        "/v1/sites",            # + /v1/sites/{id}/clients, /devices, /networks, /firewall, /wifi …
        "/v1/info",
        "/v1/pending-devices",
        "/v1/dpi",
        "/v1/countries",
    ),
    "freshdesk": (              # Freshdesk API v2 reads (D-83)
        "/tickets",
        "/contacts",
        "/companies",
        "/agents",
        "/groups",
        "/search",              # search/tickets, search/contacts, search/companies
        "/solutions",           # KB categories/folders/articles
        "/ticket_fields",
        "/contact_fields",
        "/company_fields",
        "/time_entries",
        "/satisfaction_ratings",
        "/canned_responses",
        "/settings",
    ),
}


def _custom_prefixes(vendor: str):
    """Read allowlist for an owner-defined custom integration (D-27). None if unknown;
    an empty tuple means the owner allowed nothing → fail closed."""
    try:
        from ..core.custom_integrations import get_store
        ci = get_store().get(vendor)
    except Exception:
        return None
    return tuple(ci.read_paths) if ci is not None else None


def is_allowed_read(vendor: str, path: str) -> tuple[bool, str]:
    """Return (allowed, reason). Fail-closed on anything not provably safe."""
    prefixes = READ_SCOPES.get(vendor)
    if prefixes is None:
        prefixes = _custom_prefixes(vendor)
    if prefixes is None:
        return False, f"unknown vendor '{vendor}'"
    if not prefixes:
        return False, (f"'{vendor}' has no readable paths configured — add read path prefixes "
                       "on its integration card first")
    if not isinstance(path, str) or not path.startswith("/"):
        return False, "path must be a string beginning with '/'"
    if "://" in path or path.startswith("//") or ".." in path:
        return False, "path may not contain a scheme, '//', or '..' (no host escape)"
    # boundary-aware prefix match: "/account" matches "/account" and "/account/..",
    # but NOT "/account_settings".
    base = _strip_beta(path.split("?", 1)[0])
    if not any(_matches(base, p) for p in prefixes):
        return False, (f"path '{path}' is not in the {vendor} read allowlist "
                       f"(allowed prefixes: {', '.join(prefixes)})")
    return True, "ok"


def _strip_beta(path: str) -> str:
    """'/beta/...' opts a Graph call into the beta endpoint (D-60); the allowlist judges the
    REST of the path — the version prefix never widens the reachable surface."""
    return path[len("/beta"):] if path.startswith("/beta/") else path


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


# ── gated writes (D-40) ──────────────────────────────────────────────────────────────────────
# scoped_write bounds the REACHABLE SURFACE only; whether a write may run at all is decided
# upstream in dispatch() (CATEGORY=write ⇒ Capability Console allow_write + approval policy).
# POST/PATCH only — destructive verbs (DELETE) are deliberately not implemented here.
WRITE_SCOPES: dict[str, tuple[str, ...]] = {
    "m365": (                   # Graph v1.0 (delegated; needs write scopes consented — D-40)
        "/users",               # create user, update user, authentication methods
        "/groups",              # Entra groups: create, add members (D-56)
        "/deviceManagement",    # Autopilot: import hashes, update tag/user (D-56)
    ),
}
_WRITE_METHODS = ("POST", "PATCH")


def is_allowed_write(vendor: str, path: str, method: str = "POST") -> tuple[bool, str]:
    """Return (allowed, reason). Fail-closed on anything not provably safe."""
    if str(method).upper() not in _WRITE_METHODS:
        return False, f"method '{method}' not allowed (only {'/'.join(_WRITE_METHODS)})"
    prefixes = WRITE_SCOPES.get(vendor)
    if not prefixes:
        return False, f"'{vendor}' has no writable paths — writes are opt-in per vendor (D-40)"
    if not isinstance(path, str) or not path.startswith("/"):
        return False, "path must be a string beginning with '/'"
    if "://" in path or path.startswith("//") or ".." in path:
        return False, "path may not contain a scheme, '//', or '..' (no host escape)"
    base = _strip_beta(path.split("?", 1)[0])
    if not any(_matches(base, p) for p in prefixes):
        return False, (f"path '{path}' is not in the {vendor} write allowlist "
                       f"(allowed prefixes: {', '.join(prefixes)})")
    return True, "ok"


def scoped_write(ctx, vendor: str, path: str, body: Optional[dict] = None,
                 method: str = "POST") -> Any:
    """Validate a write against the allowlist, then call the tenant's vendor client.
    Only reachable from CATEGORY=write tools, which dispatch() gates behind the owner's
    allow_write + approval policy — this function bounds WHERE such a tool can write."""
    allowed, reason = is_allowed_write(vendor, path, method)
    if not allowed:
        return {"error": f"write blocked: {reason}"}
    fn = getattr(ctx.client(vendor), method.lower(), None)
    if not callable(fn):
        return {"error": f"the '{vendor}' client does not support {method} yet"}
    return fn(path, body or {})


# ── gated DELETEs (D-65) ─────────────────────────────────────────────────────────────────────
# DELETE removes an object (group, membership, auth method, autopilot device). Bounded to its own
# allowlist; still only reachable from an owner-approved CATEGORY=write tool. Mailbox-CONTENT
# deletion is NOT here — that stays the EXO destructive path (D-54).
DELETE_SCOPES: dict[str, tuple[str, ...]] = {
    "m365": (
        "/groups",                       # delete a group, or a member $ref under it
        "/users",                        # a user's auth methods (phoneMethods/{id})
        "/deviceManagement",             # an autopilot device identity
    ),
}


def is_allowed_delete(vendor: str, path: str) -> tuple[bool, str]:
    prefixes = DELETE_SCOPES.get(vendor)
    if not prefixes:
        return False, f"'{vendor}' has no deletable paths (deletes are opt-in per vendor)"
    if not isinstance(path, str) or not path.startswith("/"):
        return False, "path must be a string beginning with '/'"
    if "://" in path or path.startswith("//") or ".." in path:
        return False, "path may not contain a scheme, '//', or '..' (no host escape)"
    base = _strip_beta(path.split("?", 1)[0])
    if not any(_matches(base, p) for p in prefixes):
        return False, (f"path '{path}' is not in the {vendor} delete allowlist "
                       f"(allowed prefixes: {', '.join(prefixes)})")
    return True, "ok"


def scoped_delete(ctx, vendor: str, path: str) -> Any:
    allowed, reason = is_allowed_delete(vendor, path)
    if not allowed:
        return {"error": f"delete blocked: {reason}"}
    fn = getattr(ctx.client(vendor), "delete", None)
    if not callable(fn):
        return {"error": f"the '{vendor}' client does not support DELETE yet"}
    return fn(path)
