"""Start a Cylance Optics InstaQuery hunt (D-82)."""
from __future__ import annotations

from typing import Any

NAME = "cylance_create_instaquery"
DESCRIPTION = ("Start a Cylance OPTICS InstaQuery — a live hunt across endpoints for an artifact "
               "(File, Process, NetworkConnection, RegistryKey, etc.). Give a `name`, the "
               "`artifact` type, the `match_type` (Exact/Fuzzy), the field `match_value_type` "
               "(e.g. File.Path, File.Sha256, Process.Name), and one or more `match_values`. "
               "Read results later with cylance_read on /instaquery/v2/{id}/results.")
SOURCE = "cylance"
CATEGORY = "write"            # creates a hunt job (not destructive, but an action)
RISK_LEVEL = "low"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_ARTIFACTS = ("File", "Process", "NetworkConnection", "RegistryKey", "Thread", "User")
_MATCH = ("Exact", "Fuzzy")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "a name for the query"},
        "artifact": {"type": "string", "enum": list(_ARTIFACTS), "description": "the artifact type"},
        "match_value_type": {"type": "string",
                             "description": "the facet to match, e.g. File.Path, File.Sha256, Process.Name"},
        "match_values": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                         "description": "the value(s) to hunt for"},
        "match_type": {"type": "string", "enum": list(_MATCH), "description": "Exact or Fuzzy (default Exact)"},
        "description": {"type": "string", "description": "optional description"},
        "case_sensitive": {"type": "boolean", "description": "case-sensitive match (default false)"},
    },
    "required": ["name", "artifact", "match_value_type", "match_values"],
    "additionalProperties": False,
}


def run(ctx, name: str, artifact: str, match_value_type: str, match_values: Any,
        match_type: str = "Exact", description: str = "", case_sensitive: bool = False, **_: Any):
    import re
    nm = (name or "").strip()
    art = (artifact or "").strip()
    mvt = (match_value_type or "").strip()
    mt = (match_type or "Exact").strip().title()
    if not nm:
        return {"ok": False, "error": "give a query name"}
    if art not in _ARTIFACTS:
        return {"ok": False, "error": "artifact must be one of: " + ", ".join(_ARTIFACTS)}
    if not re.match(r"^[A-Za-z0-9._]{1,64}$", mvt):
        return {"ok": False, "error": "match_value_type is not valid (e.g. File.Path)"}
    if mt not in _MATCH:
        return {"ok": False, "error": "match_type must be Exact or Fuzzy"}
    vals = [str(v).strip() for v in match_values if str(v or "").strip()] \
        if isinstance(match_values, list) else []
    if not vals:
        return {"ok": False, "error": "give at least one match value"}
    body = {"name": nm[:256], "description": (description or "")[:500], "artifact": art,
            "match_value_type": mvt, "match_values": vals[:20], "case_sensitive": bool(case_sensitive),
            "match_type": mt}
    r = ctx.client("cylance").write("POST", "/instaquery/v2", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "query": nm, "created": r,
            "note": "InstaQuery started — read results with cylance_read on /instaquery/v2/{id}/results"}
