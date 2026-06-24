"""Set a mailbox's max send/receive message size (D-55; SOP: exchange-online)."""
from __future__ import annotations

import re
from typing import Any

NAME = "exo_set_mailbox_quota"
DESCRIPTION = ("Change a mailbox's maximum SEND and/or RECEIVE message size. Sizes like '35MB' "
               "or '100MB' (Exchange Online hard ceiling: 150MB). To CHECK the current sizes "
               "use exo_mailbox_details. Verifies the change before reporting success. Pass "
               "`identities` (a list) to act on MANY mailboxes in ONE call — do NOT call this "
               "tool once per mailbox.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identity": {"type": "string", "description": "the mailbox's primary email address"},
        "identities": {"type": "array", "items": {"type": "string"},
                       "description": "act on MANY in ONE call — a list of mailbox addresses; "
                                      "results come back together. Use this instead of calling "
                                      "the tool once per mailbox."},
        "max_send": {"type": "string", "description": "new max SEND size, e.g. '35MB' (optional)"},
        "max_receive": {"type": "string",
                        "description": "new max RECEIVE size, e.g. '36MB' (optional)"},
    },
    "required": [],
    "additionalProperties": False,
}

_SIZE = re.compile(r"^(\d+(?:\.\d+)?)\s*(KB|MB|GB)$", re.IGNORECASE)
_TO_MB = {"KB": 1 / 1024, "MB": 1.0, "GB": 1024.0}


def _parse(label: str, raw: str):
    """Return (canonical_size, None) or (None, error). Bounds: 1 MB – 150 MB (EXO ceiling).
    Accepts KB/MB/GB (e.g. '0.1GB' = ~102MB) — Exchange itself accepts the same units."""
    m = _SIZE.match((raw or "").strip())
    if not m:
        return None, f"{label}: '{raw}' is not a size — use e.g. '35MB' or '0.1GB'"
    n, unit = float(m.group(1)), m.group(2).upper()
    mb = n * _TO_MB[unit]
    if not 1 <= mb <= 150:
        return None, (f"{label}: {raw} is outside Exchange Online's 1MB–150MB range "
                      f"(that's ~{mb:.0f}MB)")
    # send Exchange a canonical "<int>MB" so the verify echo-match is exact
    return f"{round(mb)}MB", None


def run(ctx, identity: str = "", max_send: str = "", max_receive: str = "",
        identities: Any = None, **_: Any):
    exo = ctx.client("exo")
    wanted = [str(x).strip() for x in (identities or []) if str(x).strip()]
    if wanted:                                          # batch (D-110) — one call, many mailboxes
        results = [_one(exo, x, max_send, max_receive) for x in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "quota_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(exo, identity, max_send, max_receive)


def _one(exo, identity: str, max_send: str = "", max_receive: str = "") -> dict:
    from . import _exo_common as c
    params: dict[str, Any] = {}
    verify: dict[str, Any] = {}
    for label, field, raw in (("max_send", "MaxSendSize", max_send),
                              ("max_receive", "MaxReceiveSize", max_receive)):
        if (raw or "").strip():
            size, e = _parse(label, raw)
            if e:
                return {"ok": False, "identity": identity, "error": e}
            params[field] = size
            verify[field] = size
    if not params:
        return {"ok": False, "identity": identity,
                "error": "give max_send and/or max_receive (e.g. '35MB')"}
    r = c.set_and_verify(exo, identity, params, verify, label="set message size limits")
    r.setdefault("identity", identity)
    return r
