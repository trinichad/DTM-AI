"""Create (and start) a Purview Content Search across mailboxes (D-115; SOP: exchange-online).

Phase 1 of Content Search. Builds a KQL ContentMatchQuery from friendly fields (or a raw `kql`),
runs New-ComplianceSearch on the Security & Compliance endpoint (via invoke_compliance), verifies
it exists (never report an unverified write — D-43), then Start-ComplianceSearch unless start:false.
Check progress with exo_content_search_status; sample hits with exo_content_search_preview.
"""
from __future__ import annotations

import datetime
from typing import Any

NAME = "exo_content_search_create"
DESCRIPTION = (
    "Create and start a Microsoft Purview Content Search across one or more mailboxes in THIS "
    "client. Build the query from `keywords`, `from_address`, `to_address`, `participants` "
    "(an address that is the sender OR any recipient), `subject`, `date_from`/`date_to` "
    "(YYYY-MM-DD), `has_attachment` — or pass a raw `kql` string for full control. Choose WHERE to "
    "search with `mailboxes` (a list of addresses) OR `all_mailboxes:true` to scan the whole tenant "
    "(you must opt in — there is no accidental tenant-wide default). Returns the search name; check "
    "progress with exo_content_search_status and sample matches with exo_content_search_preview. "
    "NOTE: this reads other people's mail and requires the eDiscovery Manager / Compliance Search "
    "role on the signed-in admin.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"            # searching tenant mail content is sensitive
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string",
                 "description": "search name (optional — a timestamped MSPAI-CS-… name is generated)"},
        "mailboxes": {"type": "array", "items": {"type": "string"},
                      "description": "WHERE to search: a list of mailbox addresses. Either this or "
                                     "all_mailboxes is required."},
        "all_mailboxes": {"type": "boolean",
                          "description": "search EVERY mailbox in the tenant — must be set "
                                         "explicitly; there is no implicit all-mailboxes default"},
        "keywords": {"type": "string",
                     "description": "free-text keywords; you may use AND / OR / NOT and quotes"},
        "from_address": {"type": "string", "description": "messages FROM this address (from:)"},
        "to_address": {"type": "string", "description": "messages TO this address (to:)"},
        "participants": {"type": "string",
                         "description": "this address appears anywhere — sender OR any recipient"},
        "subject": {"type": "string", "description": "subject contains this phrase"},
        "date_from": {"type": "string", "description": "received on/after this date (YYYY-MM-DD)"},
        "date_to": {"type": "string", "description": "received on/before this date (YYYY-MM-DD)"},
        "has_attachment": {"type": "boolean", "description": "only messages with attachments"},
        "kql": {"type": "string",
                "description": "raw KQL ContentMatchQuery — overrides all the fields above"},
        "start": {"type": "boolean",
                  "description": "start the search immediately (default true); false just creates it"},
    },
    "required": [],
    "additionalProperties": False,
}


def run(ctx, name: str = "", mailboxes: Any = None, all_mailboxes: bool = False,
        keywords: str = "", from_address: str = "", to_address: str = "", participants: str = "",
        subject: str = "", date_from: str = "", date_to: str = "", has_attachment: Any = None,
        kql: str = "", start: bool = True, **_: Any):
    from . import _content_search as cs

    # 1. locations — require an explicit list OR an explicit all_mailboxes opt-in (no accidental "All")
    boxes = [str(x).strip() for x in (mailboxes or []) if str(x).strip()]
    if not boxes and not all_mailboxes:
        return {"ok": False, "error": "give `mailboxes` (a list of addresses) or set "
                                      "`all_mailboxes:true` — there is no tenant-wide default"}
    if boxes and all_mailboxes:
        return {"ok": False, "error": "pass EITHER `mailboxes` OR `all_mailboxes:true`, not both"}

    # 2. query — empty matches ALL items, so refuse it (no accidental mailbox dump)
    query = cs.build_kql(keywords=keywords, from_address=from_address, to_address=to_address,
                         participants=participants, subject=subject, date_from=date_from,
                         date_to=date_to, has_attachment=has_attachment, raw_kql=kql)
    if not query.strip():
        return {"ok": False, "error": "give at least one search criterion "
                                      "(keywords / from_address / to_address / participants / "
                                      "subject / date / has_attachment) or a raw `kql` — an empty "
                                      "query would match EVERY item"}

    name = (name or "").strip() or "MSPAI-CS-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    exo = ctx.client("exo")

    params: dict[str, Any] = {"Name": name, "ContentMatchQuery": query,
                              "ExchangeLocation": "All" if all_mailboxes else boxes}
    if not all_mailboxes:
        params["AllowNotFoundExchangeLocationsEnabled"] = True   # tolerate an alias/group address

    r = exo.invoke_compliance("New-ComplianceSearch", params)
    e = cs_err(r)
    if e:
        return {"ok": False, "step": "create", "error": e + cs.role_hint(e)}

    # verify it exists before reporting success (D-43)
    chk = exo.invoke_compliance("Get-ComplianceSearch", {"Identity": name})
    if cs_err(chk) or not cs.rows(chk):
        return {"ok": False, "step": "verify",
                "error": "New-ComplianceSearch returned no error but the search could not be read "
                         "back — check the Purview portal"}

    started = False
    if start:
        sr = exo.invoke_compliance("Start-ComplianceSearch", {"Identity": name})
        se = cs_err(sr)
        if se:
            return {"ok": False, "step": "start", "name": name, "query": query,
                    "error": f"the search was created but could not be started: {se}"
                             + cs.role_hint(se)}
        started = True

    after = cs.rows(exo.invoke_compliance("Get-ComplianceSearch", {"Identity": name}))
    status = str(after[0].get("Status")) if after else ("Starting" if started else "NotStarted")
    return {"ok": True, "name": name, "query": query,
            "locations": "All mailboxes" if all_mailboxes else boxes,
            "started": started, "status": status,
            "note": ("search started — it estimates in the background; check progress with "
                     "exo_content_search_status, then sample matches with "
                     "exo_content_search_preview")
                    if started else
                    "search created but NOT started — run it with start:true when ready"}


def cs_err(r: Any) -> str:
    return str(r.get("error")) if isinstance(r, dict) and r.get("error") else ""
