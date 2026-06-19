"""Installed software on a Kaseya-managed machine (D-68; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_installed_software"
DESCRIPTION = ("List the software on ONE machine from Kaseya's audit: installed applications, "
               "Add/Remove Programs entries, and inventoried software licenses. Pass the machine "
               "name or AgentId. Use for 'what's installed on X' or 'which apps/versions does X "
               "have'.")
SOURCE = "kaseya"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent name or AgentId"},
        "include": {"type": "string", "enum": ["applications", "addremove", "licenses", "all"],
                    "description": "which view (default applications)"},
    },
    "required": ["machine"],
    "additionalProperties": False,
}

_APP = ("ApplicationName", "ProductName", "Name", "Version", "Publisher", "Manufacturer",
        "DirectoryPath", "InstallDate")
_LIC = ("PublisherName", "ProductName", "Name", "LicenseCode", "ProductKey", "Version")


def _slim_list(rows, fields):
    from . import _kaseya_common as k
    return [k.slim(r, fields) for r in k.rows(rows)]


def run(ctx, machine: str, include: str = "applications", **_: Any):
    from . import _kaseya_common as k
    client = ctx.client("kaseya")
    agent, err = k.resolve_agent(client, machine)
    if err:
        return {"ok": False, "error": err}
    aid = agent.get("AgentId")
    want = (include or "applications").strip().lower()
    out: dict[str, Any] = {"ok": True,
                           "machine": agent.get("AgentName") or agent.get("ComputerName"),
                           "agent_id": aid}
    errors = []
    if want in ("applications", "all"):
        d, e = k.result(client, f"/assetmgmt/audit/{aid}/software/installedapplications")
        out["applications"] = _slim_list(d, _APP) if not e else []
        if e:
            errors.append(f"applications: {e}")
    if want in ("addremove", "all"):
        d, e = k.result(client, f"/assetmgmt/audit/{aid}/software/addremoveprograms")
        out["add_remove_programs"] = _slim_list(d, _APP) if not e else []
        if e:
            errors.append(f"addremove: {e}")
    if want in ("licenses", "all"):
        d, e = k.result(client, f"/assetmgmt/audit/{aid}/software/licenses")
        out["licenses"] = _slim_list(d, _LIC) if not e else []
        if e:
            errors.append(f"licenses: {e}")
    # all sections errored → it's a real failure, not an empty machine
    if errors and len(errors) == sum(1 for key in ("applications", "add_remove_programs",
                                                   "licenses") if key in out):
        return {"ok": False, "error": "; ".join(errors)}
    if errors:
        out["partial_errors"] = errors
    return out
