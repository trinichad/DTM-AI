"""A Kaseya machine's security posture — AV/firewall + local admins (D-68; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_security_posture"
DESCRIPTION = ("Show ONE machine's security posture from Kaseya: detected antivirus / firewall "
               "SECURITY PRODUCTS, and the LOCAL ADMINISTRATOR accounts on the box. Pass the "
               "machine name or AgentId. Use for 'what AV is on X', 'who are the local admins "
               "on X'. (For the cloud EDR view use Cylance/Huntress tools.)")
SOURCE = "kaseya"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent name or AgentId"},
    },
    "required": ["machine"],
    "additionalProperties": False,
}

_SEC = ("ProductType", "ProductName", "DisplayName", "CompanyName", "Publisher", "Version",
        "ProductState", "Enabled", "UpToDate")
_USER = ("UserName", "Name", "FullName", "Disabled", "IsDisabled", "Description")
_MEMBER = ("MemberName", "UserName", "Name", "GroupName", "Domain")


def run(ctx, machine: str, **_: Any):
    from . import _kaseya_common as k
    client = ctx.client("kaseya")
    agent, err = k.resolve_agent(client, machine)
    if err:
        return {"ok": False, "error": err}
    aid = agent.get("AgentId")
    out: dict[str, Any] = {"ok": True,
                           "machine": agent.get("AgentName") or agent.get("ComputerName"),
                           "agent_id": aid}
    errors = []

    sec, e = k.result(client, f"/assetmgmt/audit/{aid}/software/securityproducts")
    out["security_products"] = [k.slim(r, _SEC) for r in k.rows(sec)] if not e else []
    if e:
        errors.append(f"security_products: {e}")

    # local administrators: the members of the local Administrators group. VSA 9 exposes local
    # group members at /assetmgmt/audit/{aid}/members (NOT /localusergroupmembers, which 404s).
    members, e = k.result(client, f"/assetmgmt/audit/{aid}/members")
    if not e:
        admins = [k.slim(r, _MEMBER) for r in k.rows(members)
                  if "admin" in str(r.get("GroupName") or "").lower()]
        out["local_administrators"] = admins or [k.slim(r, _MEMBER) for r in k.rows(members)]
    else:
        # fall back to the full local-accounts list if the group-members endpoint isn't present
        accts, e2 = k.result(client, f"/assetmgmt/audit/{aid}/useraccounts")
        out["local_accounts"] = [k.slim(r, _USER) for r in k.rows(accts)] if not e2 else []
        if e2:
            errors.append(f"local_accounts: {e} / {e2}")

    if errors and not out.get("security_products") and not out.get("local_administrators") \
            and not out.get("local_accounts"):
        return {"ok": False, "error": "; ".join(errors)}
    if errors:
        out["partial_errors"] = errors
    return out
