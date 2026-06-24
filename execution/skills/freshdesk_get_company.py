"""Get one Freshdesk company's detail (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_get_company"
DESCRIPTION = ("Get the full detail of one Freshdesk company by `company_id`. Pass `company_id` "
               "for one or `company_ids` (a list) to fetch MANY in ONE call — do NOT call this "
               "tool once per company.")
SOURCE = "freshdesk"
GROUP = "freshdesk_contacts"
CATEGORY = "read"
RISK_LEVEL = "low"
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_id": {"type": "integer", "description": "the Freshdesk company id"},
        "company_ids": {"type": "array", "items": {"type": "integer"},
                        "description": "fetch MANY companies in ONE call — a list of company ids; "
                                       "results come back together. Use this instead of calling "
                                       "the tool once per company."},
    },
    "additionalProperties": False,
}


def run(ctx, company_id: Any = None, company_ids: Any = None, **_: Any):
    wanted = [int(x) for x in (company_ids or [])]
    if wanted:                                         # batch (D-110) — one call, many companies
        results = [_one(ctx, c) for c in wanted[:500]]
        return {"ok": any(r.get("ok") for r in results), "companies_done": len(results),
                "ok_count": sum(1 for r in results if r.get("ok")), "results": results}
    return _one(ctx, company_id)


def _one(ctx, company_id: int) -> dict:
    return ctx.client("freshdesk").get(f"/companies/{int(company_id)}")
