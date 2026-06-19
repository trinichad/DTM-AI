"""Cylance Optics detections — list (D-82)."""
from __future__ import annotations

from typing import Any

NAME = "cylance_list_detections"
DESCRIPTION = ("List Cylance OPTICS (EDR) detections for this client — behavioral detections beyond "
               "file threats, with severity, status, device, and rule. Pass `severity` "
               "(Informational/Low/Medium/High) to filter.")
SOURCE = "cylance"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "severity": {"type": "string", "enum": ["Informational", "Low", "Medium", "High"],
                     "description": "filter by severity (optional)"},
    },
    "additionalProperties": False,
}
_FIELDS = ("Id", "Severity", "Status", "Description", "DetectedOn", "Device", "DeviceName",
           "RuleName", "DetectionRule")


def run(ctx, severity: str = "", **_: Any):
    params = {}
    if (severity or "").strip():
        params["severity"] = severity.strip()
    out = []
    for d in ctx.client("cylance").get_paginated("/detections/v2", params or None):
        out.append({k: d.get(k) for k in _FIELDS if k in d} or d)
    return out
