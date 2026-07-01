"""Preview a Content Search's matching items (D-115; SOP: exchange-online).

Idempotent preview lifecycle on the Security & Compliance endpoint:
  - <name>_Preview action Completed → parse + return a SAMPLE of matching items (Purview caps this).
  - action exists but not done       → "still preparing, run again shortly".
  - search estimate not Completed yet → "wait for the estimate, then preview".
  - otherwise                         → New-ComplianceSearchAction -Preview to start it.

This is the closest to "see the results" without exporting. The preview action's Results string is
parsed best-effort and the raw string is always included (the format can drift — D-115 SOP).
Export/download is phase 2 (and structurally blocked here: the param allowlist forbids -Export).
"""
from __future__ import annotations

from typing import Any

NAME = "exo_content_search_preview"
DESCRIPTION = (
    "Preview the items a Microsoft Purview Content Search matched — a SAMPLE of messages with "
    "sender, subject, received date, mailbox and size, so you can sanity-check the search before "
    "exporting. Pass the search `name`. Idempotent: the first call starts the preview, and calling "
    "it again a minute later returns the sample results. The search must have finished estimating "
    "first (see exo_content_search_status). Purview limits how many items a preview shows.")
SOURCE = "m365"
CATEGORY = "write"            # the first call STARTS a preview action (New-ComplianceSearchAction)
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "the content search name to preview"},
    },
    "required": ["name"],
    "additionalProperties": False,
}


def run(ctx, name: str = "", **_: Any):
    from . import _content_search as cs
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "give the content search `name` to preview"}
    exo = ctx.client("exo")
    action_id = f"{name}_Preview"

    # 1. does a preview action already exist?
    existing = exo.invoke_compliance("Get-ComplianceSearchAction",
                                     {"Identity": action_id, "Details": True})
    e = _err(existing)
    if e and not _not_found(e):
        return {"ok": False, "step": "read", "error": e + cs.role_hint(e)}
    act = cs.rows(existing) if not e else []
    if act:
        status = str(act[0].get("Status") or "")
        if status.lower() != "completed":
            return {"ok": True, "name": name, "preview_status": status or "InProgress",
                    "note": "preview is still being prepared — run exo_content_search_preview "
                            "again in a minute"}
        items = cs.parse_preview(act[0].get("Results"))
        return {"ok": True, "name": name, "preview_status": "Completed",
                "item_count": len(items), "items": items,
                "raw_results": str(act[0].get("Results") or "")[:4000],
                "note": "this is a SAMPLE of matching items (Purview caps preview size); use "
                        "exo_content_search_status for the full estimate"}

    # 2. no preview yet — the search must have finished estimating first
    raw = exo.invoke_compliance("Get-ComplianceSearch", {"Identity": name})
    srch = [] if _err(raw) else cs.rows(raw)               # don't mistake an error envelope for a row
    if not srch:
        return {"ok": False, "step": "locate",
                "error": f"no content search named '{name}' — create one with "
                         f"exo_content_search_create"}
    sstatus = str(srch[0].get("Status") or "")
    if sstatus.lower() != "completed":
        return {"ok": True, "name": name, "search_status": sstatus or "InProgress",
                "note": f"the search estimate isn't finished yet (status: "
                        f"{sstatus or 'InProgress'}) — wait, then run preview again"}

    # 3. start the preview
    r = exo.invoke_compliance("New-ComplianceSearchAction", {"SearchName": name, "Preview": True})
    se = _err(r)
    if se:
        return {"ok": False, "step": "start_preview", "error": se + cs.role_hint(se)}
    return {"ok": True, "name": name, "preview_status": "Starting",
            "note": "preview started — run exo_content_search_preview again in ~1 minute to see "
                    "the sample results"}


def _err(r: Any) -> str:
    return str(r.get("error")) if isinstance(r, dict) and r.get("error") else ""


def _not_found(e: str) -> bool:
    e = (e or "").lower()
    return any(s in e for s in ("not found", "couldn't be found", "wasn't found", "does not exist"))
