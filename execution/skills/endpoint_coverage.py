"""Cross-vendor endpoint coverage — join Kaseya / Cylance / Huntress by hostname (server-side).

Answers "do these machines have Cylance & Huntress installed, and what versions" in ONE call, so the
model doesn't have to reconcile three large vendor lists itself (which small models fail at). The join
is deterministic code; the model just formats the returned table. Also surfaces coverage GAPS (machines
in Kaseya but missing an EDR/MDR agent) — a genuinely useful MSP audit.
"""
from __future__ import annotations

from typing import Any

NAME = "endpoint_coverage"
DESCRIPTION = (
    "Cross-reference machines across Kaseya, Cylance, and Huntress by hostname and report security-agent "
    "coverage in ONE call. REQUIRES name_contains (a hostname substring like 'rho', or a site/group "
    "token). Returns one row per machine — hostname, in Kaseya (and online), Cylance installed (+ agent "
    "version), Huntress installed (+ version) — plus a summary and the gaps (machines missing Cylance or "
    "Huntress). USE THIS for 'do these machines have Cylance/Huntress and what versions' instead of "
    "listing each vendor separately; it does the join for you.")
SOURCE = "multi"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name_contains": {"type": "string",
                          "description": "case-insensitive hostname substring to scope the cross-reference (e.g. 'rho')"}
    },
    "required": ["name_contains"],
    "additionalProperties": False,
}

_KASEYA_NAME_FIELDS = ("ComputerName", "AgentName", "DisplayName", "AssetName")
_KASEYA_HAY = ("AgentName", "ComputerName", "DisplayName", "AssetName", "MachineGroup", "GroupName")


def _key(name: Any) -> str:
    """Normalize a hostname to a join key: base name before the first dot, upper-cased.
    e.g. 'rho-9sn4xd3.root.rho' -> 'RHO-9SN4XD3'; Cylance 'RHO-9SN4XD3' and Huntress 'RHO-9SN4XD3' align."""
    return str(name or "").split(".")[0].strip().upper()


def _kaseya_name(a: dict) -> str:
    for k in _KASEYA_NAME_FIELDS:
        if a.get(k):
            return str(a[k])
    return ""


def run(ctx, name_contains: str, **_: Any):
    needle = (name_contains or "").strip().lower()
    rows: dict[str, dict] = {}
    errors: dict[str, str] = {}

    def row(key: str) -> dict:
        return rows.setdefault(key, {"hostname": key, "kaseya": False, "kaseya_online": None,
                                     "cylance": False, "cylance_version": None,
                                     "huntress": False, "huntress_version": None})

    # ── Kaseya agents (machine-group view) ──
    try:
        for a in ctx.client("kaseya").get_agents():
            hay = " ".join(str(a.get(k, "")) for k in _KASEYA_HAY).lower()
            if needle and needle not in hay:
                continue
            r = row(_key(_kaseya_name(a)))
            r["kaseya"] = True
            online = a.get("Online")
            if online is not None:
                r["kaseya_online"] = bool(online)
    except Exception as e:  # contained: a vendor outage shouldn't void the whole report
        errors["kaseya"] = str(e)

    # ── Cylance devices (dedup by id; pagination drifts) ──
    try:
        seen: set = set()
        for d in ctx.client("cylance").get_paginated("/devices/v2"):
            did = d.get("id")
            if did is not None and did in seen:
                continue
            if did is not None:
                seen.add(did)
            nm = d.get("name") or d.get("Name") or ""
            if needle and needle not in str(nm).lower():
                continue
            r = row(_key(nm))
            r["cylance"] = True
            r["cylance_version"] = d.get("agent_version") or d.get("agentVersion")
    except Exception as e:
        errors["cylance"] = str(e)

    # ── Huntress agents ──
    try:
        for a in ctx.client("huntress").get_paginated("/agents"):
            nm = a.get("hostname") or ""
            if needle and needle not in str(nm).lower():
                continue
            r = row(_key(nm))
            r["huntress"] = True
            r["huntress_version"] = a.get("version")
    except Exception as e:
        errors["huntress"] = str(e)

    machines = sorted((r for r in rows.values() if r["hostname"]), key=lambda r: r["hostname"])
    summary = {
        "machines": len(machines),
        "in_kaseya": sum(1 for r in machines if r["kaseya"]),
        "with_cylance": sum(1 for r in machines if r["cylance"]),
        "with_huntress": sum(1 for r in machines if r["huntress"]),
        # Only report 'missing' when that vendor's data was actually retrieved — never imply absence
        # from a failed query (Behavioral Rule #2: don't invent facts).
        "missing_cylance": (sorted(r["hostname"] for r in machines if not r["cylance"])
                            if "cylance" not in errors else None),
        "missing_huntress": (sorted(r["hostname"] for r in machines if not r["huntress"])
                             if "huntress" not in errors else None),
    }
    if errors:
        summary["data_unavailable"] = errors  # be explicit about partial data
    return {"filter": name_contains, "summary": summary, "machines": machines}
