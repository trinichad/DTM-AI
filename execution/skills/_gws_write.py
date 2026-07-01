"""Shared helpers for Google Workspace WRITE skills (D-118).

Password generation (server-side, never by the LLM — same rule as m365_create_user) and a
friendly mapper for the Admin SDK's error bodies. Writes themselves go through scopes.scoped_write /
scoped_delete, which dispatch() only reaches for an owner-approved CATEGORY=write tool.
"""
from __future__ import annotations

import json
import secrets
import string
from typing import Any


def gen_password(n: int = 16) -> str:
    """n chars with all four classes — generated here, never by the model."""
    pools = (string.ascii_uppercase, string.ascii_lowercase, string.digits, "!@#$%*")
    chars = [secrets.choice(p) for p in pools]
    chars += [secrets.choice("".join(pools)) for _ in range(max(0, n - 4))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def err_msg(e: Any) -> str:
    """Turn an HttpError from the Google API into a short, human message."""
    status = getattr(e, "status", None)
    body = getattr(e, "body", "") or ""
    msg = ""
    try:
        j = json.loads(body)
        msg = ((j.get("error") or {}).get("message")) or ""
    except (json.JSONDecodeError, AttributeError, TypeError):
        msg = body[:200]
    return f"Google API HTTP {status}: {msg or 'request failed'}".strip()


def api_error(data: Any) -> str:
    """If a client returned an inline {'error': ...} envelope (blocked path etc.), the message."""
    if isinstance(data, dict) and data.get("error"):
        return str(data["error"])
    return ""
