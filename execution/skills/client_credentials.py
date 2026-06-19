"""client_credentials — tell the agent WHICH credentials a client has, never their values (D-25).

Read-only and value-blind: returns the LABELS, field names, and append requirements for the bound
client so the agent knows what it can act with and what a human must supply — but it can NEVER read a
secret. Actual use happens server-side via a connector + CredVault.resolve(); the value never enters
the model context. ENABLED_BY_DEFAULT=False — this is part of the (gated) write-phase capability.
"""
from __future__ import annotations

from typing import Any

NAME = "client_credentials"
DESCRIPTION = ("List the credential LABELS saved for this client (e.g. 'o365_global_admin') with their "
               "field names and whether a human append is required — so you know what is available to "
               "act with. Returns NO secret values; you can only reference a credential by label for an "
               "action tool, never read it.")
SOURCE = "msp_ai"
CATEGORY = "read"
RISK_LEVEL = "none"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


def run(ctx, **_: Any):
    cv = (getattr(ctx, "_meta", None) or {}).get("credvault")
    if cv is None:
        return {"ok": False, "error": "credential vault unavailable"}
    from execution.core.credvault import VaultLocked
    try:
        return {"ok": True, "tenant": ctx.tenant_id, "credentials": cv.safe_list(ctx.tenant_id)}
    except VaultLocked:
        return {"ok": True, "tenant": ctx.tenant_id, "locked": True, "credentials": [],
                "note": "The credential vault is locked — ask the owner to unlock it before using a credential."}
