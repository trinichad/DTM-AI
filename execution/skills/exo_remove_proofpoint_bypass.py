"""Remove the Proofpoint Essentials spam bypass (D-65; SOP: exchange-online).
The opposite of exo_setup_proofpoint_bypass."""
from __future__ import annotations

from typing import Any

NAME = "exo_remove_proofpoint_bypass"
DESCRIPTION = ("Remove the PROOFPOINT ESSENTIALS spam bypass: deletes the SCL -1 transport rule "
               "and the inbound connector (created by exo_setup_proofpoint_bypass). Each part "
               "is skipped cleanly if already absent. Run when moving a client off Proofpoint.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_RULE_NAME = "By-pass Spam filtering for Proofpoint Essentials"
_CONN_NAME = "Proofpoint Essentials Inbound Connector"
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rule_name": {"type": "string", "description": f"transport rule name (default "
                                                       f"'{_RULE_NAME}')"},
        "connector_name": {"type": "string", "description": f"connector name (default "
                                                            f"'{_CONN_NAME}')"},
    },
    "additionalProperties": False,
}


def _remove(c, exo, get_cmd, rm_cmd, name, label, steps):
    cur = exo.invoke(get_cmd, {"Identity": name})
    rows = [x for x in (cur if isinstance(cur, list) else [cur]) if isinstance(x, dict)]
    if c.err(cur) or not rows:
        steps[label] = "already absent"
        return True
    r = exo.invoke(rm_cmd, {"Identity": name, "Confirm": False})
    if c.err(r):
        steps[label] = c.err(r)
        return False
    chk = exo.invoke(get_cmd, {"Identity": name})
    rows2 = [x for x in (chk if isinstance(chk, list) else [chk]) if isinstance(x, dict)]
    if not c.err(chk) and rows2:
        steps[label] = "still present after removal — check Exchange"
        return False
    steps[label] = "removed"
    return True


def run(ctx, rule_name: str = "", connector_name: str = "", **_: Any):
    from . import _exo_common as c
    exo = ctx.client("exo")
    steps: dict[str, Any] = {}
    ok = _remove(c, exo, "Get-TransportRule", "Remove-TransportRule",
                 (rule_name or "").strip() or _RULE_NAME, "spam_bypass_rule", steps)
    ok = _remove(c, exo, "Get-InboundConnector", "Remove-InboundConnector",
                 (connector_name or "").strip() or _CONN_NAME, "inbound_connector", steps) and ok
    return {"ok": ok, "steps": steps,
            **({"note": "Proofpoint bypass removed — make sure the client's MX/mail flow no "
                        "longer depends on it"} if ok else {})}
