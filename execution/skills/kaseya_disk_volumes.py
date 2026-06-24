"""Disk volumes + free space for a Kaseya machine (D-68; SOP: kaseya-vsa)."""
from __future__ import annotations

from typing import Any

NAME = "kaseya_disk_volumes"
DESCRIPTION = ("Show a machine's disk volumes and FREE SPACE from Kaseya's hardware audit "
               "(drive letter, total size, free space, % free, format). Pass `machine` for one "
               "box, or `machines` (a list) to do MANY in ONE call — do NOT call this tool once "
               "per machine. Use for 'is X low on disk' or 'how much free space on X'.")
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
    },
    "additionalProperties": False,
}

_VOL = ("DriveLetter", "Drive", "Letter", "Label", "VolumeName", "Format", "FileSystem",
        "TotalBytes", "TotalSize", "SizeBytes", "FreeBytes", "FreeSpace", "UsedBytes",
        "PercentFree", "FreePercent")


def _pct_free(v: dict):
    for k_ in ("PercentFree", "FreePercent"):
        if v.get(k_) is not None:
            return v[k_]
    total = v.get("TotalBytes") or v.get("TotalSize") or v.get("SizeBytes")
    free = v.get("FreeBytes") or v.get("FreeSpace")
    try:
        return round(int(free) / int(total) * 100, 1) if total and free else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def run(ctx, machine: str = "", machines: Any = None, **_: Any):
    wanted = [str(m).strip() for m in (machines or []) if str(m).strip()]
    if wanted:                                         # batch (D-110) — one call, many machines
        results = [_one(ctx, m) for m in wanted[:200]]
        return {"ok": any(r.get("ok") for r in results), "machines_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, machine)


def _one(ctx, machine: str) -> dict:
    from . import _kaseya_common as k
    client = ctx.client("kaseya")
    agent, err = k.resolve_agent(client, machine)
    if err:
        return {"ok": False, "machine": machine, "error": err}
    aid = agent.get("AgentId")
    data, e = k.result(client, f"/assetmgmt/audit/{aid}/hardware/diskvolumes")
    if e:
        return {"ok": False, "machine": machine, "error": e}
    vols = []
    for v in k.rows(data):
        row = k.slim(v, _VOL)
        pf = _pct_free(v)
        if pf is not None:
            row["percent_free"] = pf
        vols.append(row)
    return {"ok": True, "machine": agent.get("AgentName") or agent.get("ComputerName"),
            "agent_id": aid, "volume_count": len(vols), "volumes": vols}
