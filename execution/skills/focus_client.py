"""Lock an 'all clients' chat onto one specific client (D-52)."""
from __future__ import annotations

from typing import Any

NAME = "focus_client"
DESCRIPTION = ("Lock this conversation onto ONE specific client. Call this when the session is "
               "'All clients' (*) and the user's request is clearly about a single client (e.g. "
               "'show me Acme_Test users') — it scopes the rest of the thread to that client so "
               "per-client tools (Microsoft 365/Exchange, etc.) work and you don't mix clients. "
               "Pass the client's exact name. No-op if already focused on that client. To switch "
               "to a DIFFERENT client the user starts a new chat (picker).")
SOURCE = "msp_ai"
CATEGORY = "read"            # only narrows the session scope; touches no client system
RISK_LEVEL = "none"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "client": {"type": "string", "description": "exact registered client name to focus on"},
    },
    "required": ["client"],
    "additionalProperties": False,
}


def run(ctx, client: str, **_: Any):
    from execution.core.memory import VaultStore
    want = (client or "").strip()
    if not want:
        return {"error": "which client? pass the client name"}
    clients = VaultStore().list_clients()
    # exact, then case-insensitive match
    match = next((c for c in clients if c == want), None) \
        or next((c for c in clients if c.lower() == want.lower()), None)
    if not match:
        return {"error": f"no registered client named '{want}'. Known: {', '.join(clients) or 'none'}"}
    return {"focused": match,
            "note": f"locked this conversation to client '{match}'. The rest of this thread is "
                    f"scoped to it; the user starts a new chat to switch clients."}
