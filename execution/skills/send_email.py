"""Send an alert email via the configured Email integration (D-28)."""
from __future__ import annotations

from typing import Any

NAME = "send_email"
DESCRIPTION = ("Send an email via the configured relay. Compose a clear, professional subject "
               "line and a well-structured body yourself from the user's request — include the "
               "relevant facts/findings, not just a pointer. Write the body in MARKDOWN (tables, "
               "bold, lists welcome): it is rendered to real HTML for the recipient. Recipients "
               "are limited by the owner's allowlist; omit `to` to use the default recipient.")
SOURCE = "email"
CATEGORY = "alert"
RISK_LEVEL = "medium"
# Owner-approved default-on (2026-06-10): the recipient allowlist in the email client is the
# hard floor, and the Capability Console toggle remains the kill switch (I-4).
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "email subject line"},
        "body": {"type": "string", "description": "email body in markdown (rendered to HTML; "
                                                  "also sent as the plain-text alternative)"},
        "to": {"type": "string", "description": "recipient(s), comma-separated for multiple — ONE "
                                                "send reaches them all (optional; defaults to "
                                                "EMAIL_DEFAULT_TO; 'me' = the signed-in user)"},
        "html": {"type": "boolean", "description": "body is ALREADY raw HTML — send as-is, "
                                                   "skip the markdown render (default false)"},
    },
    "required": ["subject", "body"],
    "additionalProperties": False,
}


def run(ctx, subject: str, body: str, to: str = "", html: bool = False, **_: Any):
    import re as _re
    parts = [p.strip() for p in _re.split(r"[,;]", to or "") if p.strip()]
    if any(p.lower() in ("me", "myself") for p in parts):    # "email me (and X)" → signed-in user
        own = ((ctx._meta or {}).get("user_profile") or {}).get("email") or ""
        if not own:
            return {"ok": False, "error": "no email on file for the signed-in user — "
                                          "ask them for the address"}
        parts = [own if p.lower() in ("me", "myself") else p for p in parts]
    to = ", ".join(parts)
    from ..core.mdmail import md_to_html
    html_body = "" if html else md_to_html(body)             # D-38: markdown → rich HTML part
    return ctx.client("email").send(subject, body, to=to or None, html=bool(html),
                                    html_body=html_body)
