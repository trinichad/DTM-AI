"""Grant a current employee access to a FORMER employee's OneDrive by making them a
site-collection administrator of that personal site (D-89; SOP: sharepoint-admin).

This is the API equivalent of the SharePoint Admin Center "Manage site collection owners" step —
i.e. `Set-SPOUser -Site <oneDriveUrl> -LoginName <user> -IsSiteCollectionAdmin $true`. It needs the
client's SharePoint sign-in (separate from Graph) plus Graph to locate the OneDrive.
"""
from __future__ import annotations

from typing import Any

NAME = "m365_grant_onedrive_access"
DESCRIPTION = ("Grant a current employee/manager ACCESS to a FORMER employee's OneDrive — makes "
               "the grantee a site-collection administrator of the former employee's personal "
               "OneDrive site (full view/download/manage), and returns the OneDrive URL to open. "
               "Use when someone has left and their files need to be reachable. Needs the client's "
               "SharePoint sign-in on the M365 card (separate from Graph). Both people are given "
               "by sign-in address (UPN).")
SOURCE = "m365"              # grouped with the other Office 365 tools; client is ctx.client("spo")
CATEGORY = "write"
RISK_LEVEL = "high"            # full access to another user's entire OneDrive
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "former_employee": {"type": "string",
                            "description": "the departed user's sign-in address (UPN) — whose "
                                           "OneDrive is being opened up"},
        "grant_to": {"type": "string",
                     "description": "the sign-in address (UPN) of the person who needs access"},
    },
    "required": ["former_employee", "grant_to"],
    "additionalProperties": False,
}


def _site_url_from_drive(web_url: str) -> str:
    """A drive's webUrl points at the document library (…/personal/<seg>/Documents); the SITE
    URL site-admin needs is its parent (…/personal/<seg>)."""
    u = (web_url or "").rstrip("/")
    if u.lower().endswith("/documents"):
        u = u[: -len("/documents")].rstrip("/")
    return u


# An unlicensed former employee's OneDrive is reachable for IMMEDIATE handoff but its availability
# is time-boxed and ultimately billing-dependent — surface that so nobody treats it as permanent.
_RETENTION_TIMELINE = [
    "Unlicensed < 60 days: normally accessible once site-admin/permissions are granted.",
    "Unlicensed 60–92 days: access may become read-only.",
    "Unlicensed 93+ days: the OneDrive may be archived and inaccessible until the account is "
    "reactivated/relicensed.",
    "If billing is disabled or payment lapses, the data may eventually be deleted.",
]


def _availability(enabled: Any, licensed: Any) -> dict:
    """Frame how durable this access is, based on the former account's license state. An active
    license does not require an active license for handoff (it works either way) — but unlicensed
    access must not be assumed indefinite."""
    if licensed is None:                              # couldn't read the user's license state
        return {"former_account_licensed": None,
                "guidance": "could not read the former account's license state — do not assume the "
                            "OneDrive stays available indefinitely; copy critical files to a "
                            "durable location promptly.",
                "retention_timeline": _RETENTION_TIMELINE, "time_boxed": True}
    if licensed:
        return {"former_account_enabled": bool(enabled), "former_account_licensed": True,
                "guidance": "the former account still holds a license, so the OneDrive is durable "
                            "for now. Removing the license or deleting the account starts a "
                            "retention clock — see retention_timeline; copy critical files out "
                            "before that.",
                "retention_timeline": _RETENTION_TIMELINE, "time_boxed": False}
    return {"former_account_enabled": bool(enabled), "former_account_licensed": False,
            "guidance": "the former account is UNLICENSED — a license is NOT required for this "
                        "immediate handoff and the grant works now, but access is NOT indefinite. "
                        "Treat it as time-boxed: download/copy the needed files to a durable "
                        "location (or relicense the account) promptly — see retention_timeline.",
            "retention_timeline": _RETENTION_TIMELINE, "time_boxed": True}


def run(ctx, former_employee: str, grant_to: str, **_: Any):
    from ..clients._http import HttpError
    from ..clients.scopes import scoped_read
    from ..clients.spo import login_claim
    from ..core.credentials import MissingCredential
    from . import _graph_common as g

    former = (former_employee or "").strip()
    grantee = (grant_to or "").strip()
    if "@" not in former:
        return {"ok": False, "error": f"'{former}' is not a sign-in address (UPN)"}
    if "@" not in grantee:
        return {"ok": False, "error": f"'{grantee}' is not a sign-in address (UPN)"}

    try:
        # Both users must exist in this client (clear errors before we touch SharePoint).
        fid, bad = g.resolve_user_id(ctx, former)
        if bad:
            return bad
        _gid, bad = g.resolve_user_id(ctx, grantee)
        if bad:
            return bad

        # Former account's license/enabled state — drives the availability framing below.
        # A read failure here is non-fatal (availability just becomes "unknown").
        enabled = licensed = None
        u = scoped_read(ctx, "m365", f"/users/{fid}",
                        {"$select": "accountEnabled,assignedLicenses"})
        if not g.fail(u) and isinstance(u, dict):
            enabled = u.get("accountEnabled")
            licensed = bool(u.get("assignedLicenses"))

        # Locate the former employee's OneDrive via Graph. The drive ROOT item exposes
        # sharepointIds.siteUrl — the canonical personal-SITE URL SetSiteAdmin needs. Prefer it
        # over string-stripping '/Documents' off the library webUrl: per Microsoft's OneDrive-URL
        # docs the personal-site path isn't guaranteed (numbers/GUIDs can be appended), so the live
        # value must be read, never constructed. Fall back to the webUrl only if siteUrl is absent.
        root = scoped_read(ctx, "m365", f"/users/{fid}/drive/root",
                           {"$select": "webUrl,sharepointIds"})
        bad = g.fail(root)
        if bad:
            return bad
        sp_ids = (root or {}).get("sharepointIds") if isinstance(root, dict) else None
        web_url = (root or {}).get("webUrl") if isinstance(root, dict) else ""
        site_url = (sp_ids or {}).get("siteUrl") or _site_url_from_drive(web_url)
        if not site_url:
            return {"ok": False, "error":
                    f"no OneDrive found for {former} — it may never have been provisioned, or the "
                    f"account is deleted (its OneDrive then sits under a retention hold; recovery "
                    f"is a Global-admin task)."}
    except MissingCredential as e:                    # Graph not connected for this client
        return {"ok": False, "error": str(e)}
    except HttpError as exc:
        return g.err403(exc, "locating the OneDrive", "Sites.Read.All / User.Read.All")

    # Make the grantee a site-collection admin via SharePoint CSOM (separate sign-in).
    try:
        spo = ctx.client("spo")
    except MissingCredential as e:
        return {"ok": False, "error": str(e)}
    r = spo.set_site_admin(site_url, login_claim(grantee), True)
    if isinstance(r, dict) and r.get("error"):
        return {"ok": False, "error": r["error"]}

    availability = _availability(enabled, licensed)
    note = (f"{grantee} is now a site-collection administrator of {former}'s OneDrive and can "
            f"view/download/manage the files. Send them this URL to open it: {site_url}")
    if availability.get("time_boxed"):
        note += (" NOTE: this access is time-boxed — the former account is unlicensed/at-risk, so "
                 "copy the needed files to a durable location promptly (see `availability`).")
    return {"ok": True, "former_employee": former, "granted_to": grantee,
            "onedrive_url": site_url, "availability": availability, "note": note}
