"""system_health — a zero-dependency read tool.

Exists so the platform can be verified end-to-end (chat -> tool -> answer) with no
vendor credentials and no live integrations. Reports which integrations are configured
(fingerprint-only, never secrets) for the bound tenant.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from execution.core import credentials

NAME = "system_health"
DESCRIPTION = "Report DTM AI platform status and which integrations are configured for this client."
SOURCE = "dtm_ai"
CATEGORY = "read"
RISK_LEVEL = "none"
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


def run(ctx, **_: Any) -> dict[str, Any]:
    integrations = [
        {"name": s.integration, "label": s.label, "configured": s.configured}
        for s in credentials.status()
    ]
    return {
        "tenant_id": ctx.tenant_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "platform": "DTM AI",
        "integrations": integrations,
        "integrations_configured": sum(1 for i in integrations if i["configured"]),
    }
