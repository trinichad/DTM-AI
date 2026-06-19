"""Approval-workflow fixture (D-47): a NON-msp_ai write so it is genuinely approval-gated
(unlike memory_note, which is now floored to auto-run). Writes the vault like memory_note so the
existing propose→approve→execute assertions still observe the result."""
from typing import Any

from execution.core.memory import VaultStore

NAME = "fx_client_write"
DESCRIPTION = "gated client-system write fixture"
SOURCE = "fixture"            # NOT msp_ai → the gate's own-vault floor does not apply
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = True
PARAMETERS = {"type": "object", "properties": {"note": {"type": "string"}},
              "required": ["note"], "additionalProperties": False}


def run(ctx, note: str, **_: Any):
    return VaultStore().append_memory(ctx.tenant_id, note, ctx.actor)
