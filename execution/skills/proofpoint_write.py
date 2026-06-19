"""proofpoint_write — generic bounded write for Proofpoint Essentials (D-86)."""
from __future__ import annotations

from typing import Any

NAME = "proofpoint_write"
DESCRIPTION = ("ADVANCED: create/update Proofpoint Essentials objects the dedicated tools don't "
               "cover. Give `method` (POST/PUT), the API `path` (e.g. /orgs/{domain} to update org "
               "settings, or /orgs/{domain}/users/{email}), and the JSON `body`. Only allow-listed "
               "org/user paths are permitted; the exact command shows on the approval card. Use "
               "proofpoint_get_user/get_org first to see an object's shape.")
SOURCE = "proofpoint"
GROUP = "proofpoint"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_METHODS = ("POST", "PUT")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "method": {"type": "string", "enum": list(_METHODS)},
        "path": {"type": "string", "description": "the API path, starting with /orgs/"},
        "body": {"type": "object", "description": "the JSON request body"},
    },
    "required": ["method", "path", "body"],
    "additionalProperties": False,
}


def run(ctx, method: str, path: str, body: dict, **_: Any):
    m = (method or "").strip().upper()
    p = (path or "").strip()
    if m not in _METHODS:
        return {"ok": False, "error": "method must be POST or PUT"}
    if not p.startswith("/orgs/") or "://" in p or ".." in p:
        return {"ok": False, "error": "path must be a simple /orgs/... Essentials path"}
    if not isinstance(body, dict):
        return {"ok": False, "error": "body must be a JSON object"}
    r = ctx.client("proofpoint").write(m, p, body)        # client WRITE_RULES enforce the allowlist
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "method": m, "path": p, "result": r}
