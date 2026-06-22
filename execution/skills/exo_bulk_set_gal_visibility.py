"""Hide/show MANY mailboxes in the Global Address List in one action (D-96; SOP: exchange-online).

The one-at-a-time path (exo_set_gal_visibility per user) makes the agent burn a tool-call ROUND per
mailbox, so a 20-user cleanup blows the round cap before it finishes. This bulk tool does the whole
list in a SINGLE call (one round, one approval): per mailbox it skips those already in the desired
state, sets + verifies the rest (D-43 discipline), and flags any that still need Exchange cloud
management enabled first (D-91) — never failing the whole batch for one bad mailbox.
"""
from __future__ import annotations

from typing import Any

NAME = "exo_bulk_set_gal_visibility"
DESCRIPTION = ("Hide (or show) MULTIPLE mailboxes in the Global Address List / address book in ONE "
               "action — pass a LIST of mailbox email addresses and hidden=true to hide (or false "
               "to show). For each it skips those already in the desired state, changes + verifies "
               "the rest, and flags any that need Exchange cloud management enabled first. Use this "
               "for bulk address-book cleanup (e.g. hiding many archived 'zzz_' accounts) instead "
               "of calling exo_set_gal_visibility one mailbox at a time. Returns a per-mailbox "
               "result table.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "medium"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identities": {"type": "array", "items": {"type": "string"},
                       "description": "the mailboxes' primary email addresses (one per mailbox)"},
        "hidden": {"type": "boolean",
                   "description": "true = hide from the address book, false = show (default true)"},
    },
    "required": ["identities"],
    "additionalProperties": False,
}


def describe_approval(ctx, args: dict):
    """Plain-language approval-card preview (D-90) — the owner approves the whole batch once."""
    ids = [str(i) for i in (args.get("identities") or []) if str(i).strip()]
    hide = args.get("hidden", True)
    verb = "Hide from address book" if hide else "Show in address book"
    out = {verb: f"{len(ids)} mailbox(es)"}
    if ids:
        out["Mailboxes"] = ", ".join(ids[:8]) + (f"  … (+{len(ids) - 8} more)" if len(ids) > 8 else "")
    return out


def run(ctx, identities: list, hidden: bool = True, **_: Any):
    from . import _exo_common as c
    if not isinstance(identities, list) or not [i for i in identities if str(i).strip()]:
        return {"ok": False, "error": "provide a non-empty list of mailbox identities"}
    exo = ctx.client("exo")
    target = bool(hidden)
    results: list[dict[str, Any]] = []
    summary = {"hidden": 0, "shown": 0, "unchanged": 0, "needs_cloud_management": 0, "error": 0}
    seen: set[str] = set()
    for raw in identities:
        ident = str(raw or "").strip()
        if not ident or ident.lower() in seen:
            continue
        seen.add(ident.lower())
        mb, bad = c.get_one_mailbox(exo, ident)
        if bad:
            results.append({"identity": ident, "status": "error", "error": bad.get("error")})
            summary["error"] += 1
            continue
        who = mb.get("PrimarySmtpAddress") or ident
        if bool(mb.get("HiddenFromAddressListsEnabled")) == target:
            results.append({"identity": who, "status": "unchanged", "hidden": target})
            summary["unchanged"] += 1
            continue
        guard = c.needs_cloud_management(mb, {"HiddenFromAddressListsEnabled": target},
                                         label=("hide from GAL" if target else "show in GAL"))
        if guard:
            results.append({"identity": who, "status": "needs_cloud_management",
                            "error": guard.get("error")})
            summary["needs_cloud_management"] += 1
            continue
        r = exo.invoke("Set-Mailbox", {"Identity": ident, "Confirm": False,
                                       "HiddenFromAddressListsEnabled": target})
        if c.err(r):
            results.append({"identity": who, "status": "failed", "error": c.err(r)})
            summary["error"] += 1
            continue
        after, bad2 = c.get_one_mailbox(exo, ident)          # verify — never claim an unseen write
        if bad2 or bool(after.get("HiddenFromAddressListsEnabled")) != target:
            results.append({"identity": who, "status": "failed",
                            "error": "the change did not stick on re-read — check Exchange directly"})
            summary["error"] += 1
            continue
        st = "hidden" if target else "shown"
        results.append({"identity": who, "status": st})
        summary[st] += 1
    changed = summary["hidden"] + summary["shown"]
    note = (f"{changed} changed, {summary['unchanged']} already set, "
            f"{summary['needs_cloud_management']} need cloud management, {summary['error']} failed")
    return {"ok": True, "hidden_target": target, "count": len(results),
            "summary": summary, "results": results, "note": note}
