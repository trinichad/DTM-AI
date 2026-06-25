"""Installed software on a Kaseya-managed machine (D-68; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_installed_software"
DESCRIPTION = ("List the software on a machine from Kaseya's audit: installed applications, "
               "Add/Remove Programs entries, and inventoried software licenses. Pass `machine` "
               "for one box, or `machines` (a list) to do MANY in ONE call — do NOT call this "
               "tool once per machine. Use for 'what's installed on X' or 'which apps/versions "
               "does X have'.")
SOURCE = "kaseya"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "machine": {"type": "string", "description": "machine/agent name or AgentId"},
        "machines": {"type": "array", "items": {"type": "string"},
                     "description": "act on MANY machines in ONE call — a list of machine/agent "
                                    "names or AgentIds; results come back together. Use this "
                                    "instead of calling the tool once per machine."},
        "include": {"type": "string", "enum": ["applications", "addremove", "licenses", "all"],
                    "description": "which view (default applications)"},
    },
    "additionalProperties": False,
}

_APP = ("ApplicationName", "ProductName", "Name", "Version", "Publisher", "Manufacturer",
        "DirectoryPath", "InstallDate")
_LIC = ("PublisherName", "ProductName", "Name", "LicenseCode", "ProductKey", "Version")


def _slim_list(rows, fields):
    from . import _kaseya_common as k
    return [k.slim(r, fields) for r in k.rows(rows)]


def run(ctx, machine: str = "", machines: Any = None, include: str = "applications", **_: Any):
    wanted = [str(m).strip() for m in (machines or []) if str(m).strip()]
    if wanted:                                         # batch (D-110) — one call, many machines
        results = ctx.map_progress(wanted[:200], lambda m: _one(ctx, m, include))
        return {"ok": any(r.get("ok") for r in results), "machines_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, machine, include)


def _one(ctx, machine: str, include: str = "applications") -> dict:
    from . import _kaseya_common as k
    client = ctx.client("kaseya")
    agent, err = k.resolve_agent(client, machine)
    if err:
        return {"ok": False, "machine": machine, "error": err}
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
        return {"ok": False, "machine": machine, "error": "; ".join(errors)}
    if errors:
        out["partial_errors"] = errors
    return out
