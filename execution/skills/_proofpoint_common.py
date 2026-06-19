"""Shared plumbing for the Proofpoint Essentials skills (D-86). No NAME → invisible (I-1).

Best-effort note: the Essentials API reference is login-gated, so the sender-list attribute names
(`safe_sender_list` / `blocked_sender_list`) and a few bodies are best guesses confirmed against the
live tenant — the tools surface the API error so they can be tuned on first use.
"""
from __future__ import annotations

import re
from typing import Any

_DOMAIN_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_EMAIL_RE = re.compile(r"^[^@\s/]{1,128}@[A-Za-z0-9.-]{1,253}$")
_SENDER_RE = re.compile(r"^(?:[^@\s/]{1,128}@)?[A-Za-z0-9.*-]{1,253}$")   # email OR domain
_SENDER_ATTRS = {"safe": "safe_sender_list", "blocked": "blocked_sender_list"}


def valid_domain(s: str) -> bool:
    return bool(_DOMAIN_RE.match(str(s or "").strip()))


def valid_email(s: str) -> bool:
    return bool(_EMAIL_RE.match(str(s or "").strip()))


def valid_sender(s: str) -> bool:
    return bool(_SENDER_RE.match(str(s or "").strip()))


def rows(data: Any, *keys: str) -> list[dict]:
    """Unwrap a list payload — a bare array, or the first list value under one of `keys`/any key."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return [r for r in data[k] if isinstance(r, dict)]
        for v in data.values():
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
    return []


def mutate_sender(client, domain: str, email: str, sender: str, which: str, add: bool):
    """GET the user, add/remove `sender` on the safe/blocked list, PUT it back. Best-effort attr
    names. Returns the write result or {"error": ...}."""
    attr = _SENDER_ATTRS[which]
    user = client.get(f"/orgs/{domain}/users/{email}")
    if not isinstance(user, dict) or user.get("error"):
        return {"error": f"could not read user: {(user or {}).get('error', user)}"}
    cur = user.get(attr)
    cur = [str(x) for x in cur] if isinstance(cur, list) else []
    low = sender.strip().lower()
    if add:
        if low not in [x.lower() for x in cur]:
            cur = cur + [sender.strip()]
    else:
        cur = [x for x in cur if x.lower() != low]
    return client.write("PUT", f"/orgs/{domain}/users/{email}", {attr: cur})
