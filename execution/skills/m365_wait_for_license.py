"""Wait for a license SKU to become available in the tenant (D-106; SOP: m365-graph).
For the PAX8 buy-as-needed flow: a SKU purchased in PAX8 can take a few minutes to appear in the
tenant — this polls until a seat is free so onboarding can continue."""
from __future__ import annotations

import os
import time
from typing import Any

NAME = "m365_wait_for_license"
DESCRIPTION = ("Poll the tenant until a license SKU is AVAILABLE (has a free seat) — for the PAX8 "
               "buy-as-needed flow, where a license you just purchased takes a few minutes to show "
               "up in the tenant. Returns as soon as a seat is free, or reports not-yet-available "
               "after the time budget so you can keep waiting or check PAX8. Read-only — it never "
               "assigns anything.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "none"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "license": {"type": "string",
                    "description": "SKU part number (e.g. SPE_F1) or GUID to wait for"},
        "minutes": {"type": "number",
                    "description": "how long to wait before giving up (default 10, max 30)"},
        "min_available": {"type": "integer",
                          "description": "seats that must be free (default 1)"},
    },
    "required": ["license"],
    "additionalProperties": False,
}

_POLL_SECONDS = 30.0       # MSPAI_LICENSE_POLL_SECONDS overrides (tests set 0)


def _seats(sku: dict) -> tuple[int, int, int]:
    total = int((sku.get("prepaidUnits") or {}).get("enabled") or 0)
    used = int(sku.get("consumedUnits") or 0)
    return total, used, total - used


def run(ctx, license: str, minutes: float = 10, min_available: int = 1, **_: Any):
    from .m365_list_licenses import _fetch_skus, find_sku
    want = (license or "").strip()
    if not want:
        return {"ok": False, "error": "no license given"}
    need = max(1, int(min_available or 1))
    budget = max(0.0, min(float(minutes or 10), 30)) * 60.0
    env = os.environ.get("MSPAI_LICENSE_POLL_SECONDS")
    poll = float(env) if env not in (None, "") else _POLL_SECONDS

    start = time.monotonic()
    attempts = 0
    last_seen = None
    while True:
        attempts += 1
        rows, bad = _fetch_skus(ctx)
        if bad:
            return bad
        sku = find_sku(rows, want)
        if sku:
            total, used, avail = _seats(sku)
            last_seen = {"license": sku.get("skuPartNumber"), "total": total, "used": used,
                         "available_seats": avail}
            if avail >= need:
                return {"ok": True, "available": True, **last_seen,
                        "waited_seconds": round(time.monotonic() - start),
                        "note": f"{avail} seat(s) free — ready to assign"}
        if time.monotonic() - start + poll > budget:     # next sleep would exceed the budget
            break
        time.sleep(poll)

    waited = round(time.monotonic() - start)
    if last_seen is None:
        return {"ok": True, "available": False, "license": want, "found": False,
                "waited_seconds": waited, "attempts": attempts,
                "note": (f"'{want}' is still not in the tenant after {waited}s — if you just bought "
                         f"it in PAX8 it may need more time; run me again to keep waiting, or check "
                         f"PAX8.")}
    return {"ok": True, "available": False, "found": True, **last_seen,
            "waited_seconds": waited, "attempts": attempts,
            "note": (f"'{last_seen['license']}' is in the tenant but has no free seat "
                     f"({last_seen['used']}/{last_seen['total']} used) after {waited}s — buy more "
                     f"in PAX8, then run me again.")}
