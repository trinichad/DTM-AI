"""Create a Freshdesk knowledge-base article (D-83)."""
from __future__ import annotations

from typing import Any

NAME = "freshdesk_create_solution_article"
DESCRIPTION = ("Create a knowledge-base article in a folder. Give the `folder_id`, the `title`, "
               "and the `description` (the article body, HTML). Optional `status`: 'draft' "
               "(default) or 'published'. Tags optional.")
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
        "folder_id": {"type": "integer", "description": "the KB folder id to create in"},
        "title": {"type": "string"},
        "description": {"type": "string", "description": "article body (HTML allowed)"},
        "status": {"type": "string", "enum": list(_STATUS), "description": "draft (default) or published"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["folder_id", "title", "description"],
    "additionalProperties": False,
}


def run(ctx, folder_id: int, title: str, description: str, status: str = "draft", tags: Any = None,
        **_: Any):
    fid = int(folder_id)
    ttl = (title or "").strip()
    desc = (description or "").strip()
    if not ttl or not desc:
        return {"ok": False, "error": "give a title and a description"}
    body: dict[str, Any] = {"title": ttl[:255], "description": desc,
                            "status": _STATUS.get((status or "draft").lower(), 1)}
    if isinstance(tags, list) and tags:
        body["tags"] = [str(t).strip() for t in tags if str(t or "").strip()]
    r = ctx.client("freshdesk").write("POST", f"/solutions/folders/{fid}/articles", body)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "article": r, "note": "article created"}
