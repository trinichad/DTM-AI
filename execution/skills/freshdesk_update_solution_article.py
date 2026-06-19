"""Update a Freshdesk knowledge-base article (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_update_solution_article"
DESCRIPTION = ("Update a knowledge-base article by `article_id` — title, description (body), or "
               "status ('draft'/'published', e.g. to publish a draft). Only the fields you pass "
               "change.")
SOURCE = "freshdesk"
GROUP = "freshdesk_kb"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_STATUS = {"draft": 1, "published": 2}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "article_id": {"type": "integer", "description": "the KB article id"},
        "title": {"type": "string"},
        "description": {"type": "string", "description": "article body (HTML allowed)"},
        "status": {"type": "string", "enum": list(_STATUS)},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["article_id"],
    "additionalProperties": False,
}


def run(ctx, article_id: int, title: str = "", description: str = "", status: str = "",
        tags: Any = None, **_: Any):
    aid = int(article_id)
    body: dict[str, Any] = {}
    if (title or "").strip():
        body["title"] = title.strip()[:255]
    if (description or "").strip():
        body["description"] = description.strip()
    if (status or "").strip():
        if status.lower() not in _STATUS:
            return {"ok": False, "error": "status must be 'draft' or 'published'"}
        body["status"] = _STATUS[status.lower()]
    if isinstance(tags, list):
        body["tags"] = [str(t).strip() for t in tags if str(t or "").strip()]
    if not body:
        return {"ok": False, "error": "give at least one field to change"}
    r = ctx.client("freshdesk").write("PUT", f"/solutions/articles/{aid}", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "article_id": aid, "note": "article updated"}
