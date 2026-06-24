"""Apply a retention policy to a mailbox (D-55; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_set_retention_policy"
DESCRIPTION = ("Apply an existing RETENTION POLICY to a mailbox. The policy must already exist "
               "in the client's Exchange — list valid names with exo_list_retention_policies "
               "first. Verifies the assignment before reporting success. Pass `identities` (a "
               "list) to act on MANY mailboxes in ONE call — do NOT call this tool once per "
               "mailbox.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string", "description": "the mailbox's primary email address"},
        "identities": {"type": "array", "items": {"type": "string"},
                       "description": "act on MANY in ONE call — a list of mailbox addresses; "
                                      "results come back together. Use this instead of calling "
                                      "the tool once per mailbox."},
        "policy": {"type": "string",
                   "description": "exact retention policy name (see exo_list_retention_policies)"},
    },
    "required": ["policy"],
    "additionalProperties": False,
}


def run(ctx, identity: str = "", policy: str = "", identities: Any = None, **_: Any):
    exo = ctx.client("exo")
    wanted = [str(x).strip() for x in (identities or []) if str(x).strip()]
    if wanted:                                          # batch (D-110) — one call, many mailboxes
        results = [_one(exo, x, policy) for x in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "retention_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(exo, identity, policy)


def _one(exo, identity: str, policy: str) -> dict:
    from . import _exo_common as c
    policy = (policy or "").strip()
    # Preflight: the policy must be one of the tenant's real policies (exact name, case-insens).
    r = exo.invoke("Get-RetentionPolicy")
    if c.err(r):
        return {"ok": False, "identity": identity, "step": "list policies", "error": c.err(r)}
    rows = [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]
    names = [str(p.get("Name")) for p in rows if p.get("Name")]
    match = next((n for n in names if n.lower() == policy.lower()), None)
    if not match:
        return {"ok": False, "identity": identity,
                "error": f"no retention policy named '{policy}' in this client — "
                         f"existing policies: {', '.join(names) or '(none)'}"}
    res = c.set_and_verify(exo, identity, {"RetentionPolicy": match},
                           {"RetentionPolicy": match}, label="set retention policy")
    res.setdefault("identity", identity)
    if res.get("ok"):
        res["note"] = "the Managed Folder Assistant applies tags on its next cycle (up to ~7 days)"
    return res
