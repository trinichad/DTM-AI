"""Full admin view of one Entra / M365 user — every attribute (D-56; SOP: m365-graph).

The Graph counterpart to exo_mailbox_details: a single-user detail read that surfaces the
COMPLETE user object (not a hand-picked subset), so a follow-up question about any attribute —
account age (createdDateTime), on-prem sync, licenses, contact fields — is answerable without a
code change. List tools stay slim for context budget (D-94); detail lookups return everything.
"""
from __future__ import annotations

from typing import Any

NAME = "m365_user_details"
DESCRIPTION = ("Show a Microsoft 365 / Entra user's FULL details — WHEN THE ACCOUNT WAS CREATED "
               "(createdDateTime, i.e. account age), enabled state, member/guest type, whether it "
               "is on-prem AD-synced and when it last synced, last password change, job "
               "title/department and all contact fields, and assigned licenses. Pass `user` for "
               "one person (sign-in address / UPN) or `users` (a list) to inspect MANY in ONE "
               "call — do NOT call this tool once per person. The result includes `raw` — the "
               "COMPLETE Graph user object with every attribute — so any other detail can be read "
               "without adding a new tool. For account CREATION DATE / age, use this (not "
               "m365_list_users, which returns only a summary row). For MAILBOX details/age use "
               "exo_mailbox_details instead.")
SOURCE = "m365"
CATEGORY = "read"
RISK_LEVEL = "low"
REQUIRES_APPROVAL = False
ENABLED_BY_DEFAULT = True
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address (UPN) or object id"},
        "users": {"type": "array", "items": {"type": "string"},
                  "description": "inspect MANY users in ONE call — a list of sign-in addresses "
                                 "(or object ids); each user's details come back together. Use "
                                 "this instead of calling the tool once per user."},
    },
    "additionalProperties": False,
}

# A broad, User.Read.All-safe superset of directory attributes so `raw` is genuinely "everything"
# (Graph has no $select=*, and the default GET /users/{id} returns only ~11 fields). Deliberately
# excludes permission-gated props (signInActivity → AuditLog.Read.All, mailboxSettings, etc.) that
# would 403 the whole request; those live behind their own tools.
_SELECT = ",".join((
    "id", "userPrincipalName", "displayName", "givenName", "surname", "mail", "mailNickname",
    "otherMails", "proxyAddresses", "imAddresses",
    "accountEnabled", "createdDateTime", "creationType", "userType", "externalUserState",
    "externalUserStateChangeDateTime",
    "jobTitle", "department", "companyName", "employeeId", "employeeType", "officeLocation",
    "businessPhones", "mobilePhone", "faxNumber", "streetAddress", "city", "state", "postalCode",
    "country", "usageLocation", "preferredLanguage",
    "onPremisesSyncEnabled", "onPremisesDistinguishedName", "onPremisesDomainName",
    "onPremisesSamAccountName", "onPremisesUserPrincipalName", "onPremisesImmutableId",
    "onPremisesLastSyncDateTime", "onPremisesSecurityIdentifier",
    "lastPasswordChangeDateTime", "passwordPolicies",
    "assignedLicenses", "assignedPlans", "showInAddressList",
))


def run(ctx, user: str = "", users: Any = None, **_: Any):
    wanted = [str(u).strip() for u in (users or []) if str(u).strip()]
    if wanted:                                         # batch lookup (D-110) — one call, many users
        results = ctx.map_progress(wanted, lambda u: _one(ctx, u))
        return {"ok": True, "users_checked": len(results), "results": results}
    return _one(ctx, user)


def _one(ctx, user: str) -> dict:
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read
    from . import _graph_common as g

    ident = (user or "").strip()
    if not ident:
        return {"ok": False, "error": "no user given"}
    try:
        data = scoped_read(ctx, "m365", f"/users/{ident}", {"$select": _SELECT})
    except HttpError as exc:
        if exc.status == 404:
            return {"ok": False, "user": ident, "error": f"no user '{ident}' found in this client"}
        return {**g.err403(exc, "reading user details", "User.Read.All"), "user": ident}
    bad = g.fail(data)
    if bad:
        return {**bad, "user": ident}
    if not (isinstance(data, dict) and data.get("id")):
        return {"ok": False, "user": ident, "error": f"no user '{ident}' found in this client"}

    lic = [l.get("skuId") for l in (data.get("assignedLicenses") or []) if isinstance(l, dict)]
    return {
        "ok": True,
        "user": data.get("userPrincipalName") or ident,
        "display_name": data.get("displayName"),
        "created": data.get("createdDateTime"),          # account age (D-56)
        "account_enabled": data.get("accountEnabled"),
        "user_type": data.get("userType"),
        "dir_synced": bool(data.get("onPremisesSyncEnabled")),   # identity mastered on-prem AD?
        "on_prem_last_sync": data.get("onPremisesLastSyncDateTime"),
        "last_password_change": data.get("lastPasswordChangeDateTime"),
        "job_title": data.get("jobTitle"),
        "department": data.get("department"),
        "licenses": lic,
        # Curated fields above are the friendly summary; `raw` carries the COMPLETE user object so a
        # follow-up about any other attribute needs no code change (owner directive — details tools
        # must not filter data). Single-user view, so list slimming (D-94) doesn't apply.
        "raw": data,
    }
