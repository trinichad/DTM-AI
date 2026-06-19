"""Set up the Proofpoint Essentials spam bypass — transport rule + inbound connector
(D-58; SOP: exchange-online).

The owner's standard two-step: (1) a transport rule that sets SCL -1 for mail arriving from
the Proofpoint Essentials US data-center ranges, (2) a partner inbound connector locked to
those ranges with TLS required. IP lists are the owner's exact published Proofpoint ranges.
"""
from __future__ import annotations

from typing import Any

NAME = "exo_setup_proofpoint_bypass"
DESCRIPTION = ("Set up the PROOFPOINT ESSENTIALS spam bypass for the client: a transport rule "
               "that skips Microsoft spam filtering (SCL -1) for mail from Proofpoint's IP "
               "ranges, plus the locked-down inbound connector (TLS required, restricted to "
               "those IPs). Each part is skipped cleanly if it already exists. Run after "
               "pointing the client's MX at Proofpoint.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"            # mail-flow change — a wrong connector can affect all inbound mail
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

# Proofpoint Essentials US ranges — the owner's standard lists.
RULE_IP_RANGES = [
    "67.231.152.0/24", "67.231.153.0/24", "67.231.154.0/24", "67.231.155.0/24",
    "67.231.156.0/24", "67.231.144.0/24", "67.231.145.0/24", "67.231.146.0/24",
    "67.231.147.0/24", "67.231.148.0/24", "67.231.149.0/24", "148.163.128.0/19",
    "91.209.104.0/24", "91.207.212.0/24", "91.207.213.0/24", "62.209.50.0/24",
    "62.209.51.0/24", "185.132.180.0/24", "185.132.181.0/24", "185.132.182.0/24",
    "185.132.183.0/24", "185.183.28.0/22",
]
CONNECTOR_IP_RANGES = [
    "67.231.152.0/24", "67.231.153.0/24", "67.231.154.0/24", "67.231.155.0/24",
    "67.231.156.0/24", "67.231.144.0/24", "67.231.145.0/24", "67.231.146.0/24",
    "67.231.147.0/24", "67.231.148.0/24", "67.231.149.0/24", "91.209.104.0/24",
    "91.207.212.0/24", "91.207.213.0/24", "62.209.50.0/24", "62.209.51.0/24",
    "185.132.180.0/24", "185.132.181.0/24", "185.132.182.0/24", "185.132.183.0/24",
] + [f"148.163.{n}.0/24" for n in range(128, 160)] \
  + [f"185.183.{n}.0/24" for n in range(28, 32)]


def _exists(c, exo, cmdlet: str, name: str) -> bool:
    r = exo.invoke(cmdlet, {"Identity": name})
    rows = [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict)]
    return not c.err(r) and bool(rows)


def run(ctx, rule_name: str = "", connector_name: str = "", **_: Any):
    from . import _exo_common as c
    rule_name = (rule_name or "").strip() or _RULE_NAME
    conn_name = (connector_name or "").strip() or _CONN_NAME
    exo = ctx.client("exo")
    steps: dict[str, Any] = {}
    ok = True

    if _exists(c, exo, "Get-TransportRule", rule_name):
        steps["spam_bypass_rule"] = "already exists"
    else:
        r = exo.invoke("New-TransportRule", {"Name": rule_name,
                                             "SenderIPRanges": RULE_IP_RANGES,
                                             "SetSCL": -1, "Confirm": False})
        if c.err(r):
            steps["spam_bypass_rule"] = c.err(r)
            ok = False
        elif not _exists(c, exo, "Get-TransportRule", rule_name):
            steps["spam_bypass_rule"] = "created but could not be read back — check Exchange"
            ok = False
        else:
            steps["spam_bypass_rule"] = f"created ({len(RULE_IP_RANGES)} IP ranges, SCL -1)"

    if _exists(c, exo, "Get-InboundConnector", conn_name):
        steps["inbound_connector"] = "already exists"
    else:
        r = exo.invoke("New-InboundConnector", {
            "Name": conn_name, "Comment": "Inbound connector for Proofpoint Essentials",
            # ConnectorType MUST be Partner — RestrictDomainsToIPAddresses/RequireTls/
            # SenderIPAddresses are applied ONLY to Partner connectors (D-66). Without it the
            # IP lock-down + TLS requirement silently don't take effect.
            "ConnectorType": "Partner",
            "SenderDomains": ["*"], "RestrictDomainsToIPAddresses": True,
            "RequireTls": True, "SenderIPAddresses": CONNECTOR_IP_RANGES,
            "Confirm": False})
        if c.err(r):
            steps["inbound_connector"] = c.err(r)
            ok = False
        elif not _exists(c, exo, "Get-InboundConnector", conn_name):
            steps["inbound_connector"] = "created but could not be read back — check Exchange"
            ok = False
        else:
            steps["inbound_connector"] = (f"created (TLS required, locked to "
                                          f"{len(CONNECTOR_IP_RANGES)} Proofpoint ranges)")

    out: dict[str, Any] = {"ok": ok, "steps": steps}
    if ok:
        out["note"] = ("Proofpoint bypass in place — also disable per-mailbox junk filtering "
                       "(exo_set_junk_filter) and block auto-forwarding "
                       "(exo_block_auto_forwarding) per your standard build")
    return out
