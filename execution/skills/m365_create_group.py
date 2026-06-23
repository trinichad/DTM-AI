"""Create a directory (Entra) group — security / M365 / dynamic (D-56; SOP: m365-graph)."""
from __future__ import annotations

import re
from typing import Any

NAME = "m365_create_group"
DESCRIPTION = ("Create a DIRECTORY (Entra) group. kind='security' (plain security group), "
               "'m365' (Microsoft 365 group — also auto-provisions a SharePoint TEAM SITE), "
               "or 'dynamic' (security group whose members come from a membership_rule, e.g. "
               "(user.department -eq \"Sales\") — members can never be added manually). For "
               "an email distribution list use exo_create_distribution_group instead. "
               "Verifies before reporting success.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_KINDS = ("security", "m365", "dynamic")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "group display name"},
        "kind": {"type": "string", "enum": list(_KINDS), "description": "group type"},
        "description": {"type": "string", "description": "what the group is for (optional)"},
        "membership_rule": {"type": "string",
                            "description": "dynamic groups only — the Entra membership rule, "
                                           "e.g. (user.department -eq \"Sales\")"},
    },
    "required": ["name", "kind"],
    "additionalProperties": False,
}


def _nickname(name: str) -> str:
    nick = re.sub(r"[^A-Za-z0-9]", "", name)[:60]
    return nick or "group"


def run(ctx, name: str, kind: str, description: str = "", membership_rule: str = "",
        **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read, scoped_write
    from . import _graph_common as g
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "the group needs a name"}
    kind = (kind or "").strip().lower()
    if kind not in _KINDS:
        return {"ok": False, "error": f"kind must be one of: {', '.join(_KINDS)}"}
    rule = (membership_rule or "").strip()
    if kind == "dynamic" and not rule:
        return {"ok": False, "error": "a dynamic group needs a membership_rule, e.g. "
                                      "(user.department -eq \"Sales\")"}
    if kind != "dynamic" and rule:
        return {"ok": False, "error": "membership_rule only applies to kind='dynamic'"}

    body: dict[str, Any] = {"displayName": name, "mailNickname": _nickname(name),
                            "description": (description or "").strip() or name}
    if kind == "security":
        body.update({"securityEnabled": True, "mailEnabled": False, "groupTypes": []})
    elif kind == "m365":
        body.update({"securityEnabled": False, "mailEnabled": True,
                     "groupTypes": ["Unified"]})
    else:                                              # dynamic security group
        body.update({"securityEnabled": True, "mailEnabled": False,
                     "groupTypes": ["DynamicMembership"], "membershipRule": rule,
                     "membershipRuleProcessingState": "On"})

    try:
        existing, bad = g.resolve_group(ctx, name)
        if existing:
            return {"ok": False, "error": f"a group named '{name}' already exists "
                                          f"(id {existing.get('id')})"}
        created = scoped_write(ctx, "m365", "/groups", body=body, method="POST")
        bad = g.fail(created)
        if bad:
            return bad
        gid = str((created or {}).get("id") or "")
        # A just-created group can 404 for a few seconds (propagation) — poll, don't fail once (D-104).
        _ok, check = g.settle(
            lambda: scoped_read(ctx, "m365", f"/groups/{gid}",
                                {"$select": "id,displayName,mail,groupTypes"}),
            lambda c: isinstance(c, dict) and bool(c.get("id"))) if gid else (False, None)
    except HttpError as exc:
        return g.err403(exc, "creating the group", "Group.ReadWrite.All")

    if not (isinstance(check, dict) and check.get("id")):
        return {"ok": False, "step": "verify", "pending": True,
                "error": "the create call returned but the group could not be read back yet — "
                         "usually propagation lag; re-check in Entra shortly before retrying"}
    out: dict[str, Any] = {"ok": True, "created": name, "kind": kind, "id": check["id"],
                           "email": check.get("mail")}
    if kind == "m365":
        out["note"] = ("Microsoft 365 group created — its SharePoint team site provisions "
                       "automatically in a few minutes; add members with "
                       "m365_add_security_group_member")
    elif kind == "dynamic":
        out["note"] = ("membership is computed from the rule (can take a few minutes to "
                       "populate) — members cannot be added manually")
    else:
        out["note"] = "add members with m365_add_security_group_member"
    return out
