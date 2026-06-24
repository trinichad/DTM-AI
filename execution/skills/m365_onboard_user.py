"""Onboard / set up a user account — the full MSP sequence (D-106; SOP: m365-graph).
A composite the agent calls once, after interactively gathering the spec (licenses+apps, contact,
groups, mailbox grants, MFA phone). Each step reuses the dedicated tool and is reported separately."""
from __future__ import annotations

from typing import Any, Optional

NAME = "m365_onboard_user"
DESCRIPTION = (
    "ONBOARD / set up a user, in order, for an EXISTING account (synced from on-prem AD in a hybrid "
    "tenant, or created cloud-first here). BEFORE calling this, gather the spec WITH the owner: "
    "(1) show available licenses with m365_list_licenses and ask which to assign and which apps to "
    "include per license — for an F1 license default to ONLY 'Microsoft Entra ID P1' + 'Microsoft "
    "Intune Plan 1' (disable the rest) but ASK if more are needed; (2) if a chosen license has no "
    "free seat, tell the owner to buy it in PAX8 and use m365_wait_for_license to wait until it "
    "appears; (3) gather contact info, the security/distribution groups to join, the mailboxes to "
    "grant Full Access + Send-As, and the cell number for MFA. Then call this ONCE. Steps: create "
    "(only if the tenant is NOT hybrid and the user doesn't exist) -> assign licenses -> set contact "
    "info (SKIPPED on hybrid — AD-mastered) -> add to security + distribution groups -> grant Full "
    "Access + Send-As on each mailbox -> add the cell as an MFA phone method -> enforce MFA. Reports "
    "each step's outcome.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_CONTACT_FIELDS = ("job_title", "department", "office", "office_phone", "mobile_phone",
                   "street_address", "city", "state", "postal_code")
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address (UPN)"},
        "create_first_name": {"type": "string",
                              "description": "first name — only used to CREATE the user if it "
                                             "doesn't exist in a non-hybrid tenant"},
        "create_last_name": {"type": "string", "description": "last name — only used to create"},
        "create_display_name": {"type": "string", "description": "display name — only used to create"},
        "licenses": {"type": "array", "description": "licenses to assign", "items": {
            "type": "object", "properties": {
                "sku": {"type": "string", "description": "SKU part number or GUID"},
                "disabled_apps": {"type": "array", "items": {"type": "string"},
                                  "description": "apps to UNCHECK (everything else stays on); for "
                                                 "F1 disable all but Entra ID P1 + Intune Plan 1"}},
            "required": ["sku"], "additionalProperties": False}},
        "contact": {"type": "object", "description": "contact / profile fields (skipped on hybrid)",
                    "properties": {f: {"type": "string"} for f in _CONTACT_FIELDS},
                    "additionalProperties": False},
        "security_groups": {"type": "array", "items": {"type": "string"},
                            "description": "Entra security/M365 groups to add the user to"},
        "distribution_groups": {"type": "array", "items": {"type": "string"},
                                "description": "distribution lists to add the user to"},
        "mailboxes": {"type": "array", "items": {"type": "string"},
                      "description": "mailboxes to grant the user Full Access + Send-As on"},
        "mfa_phone": {"type": "string", "description": "the user's cell number for an MFA phone method"},
        "enforce_mfa": {"type": "boolean", "description": "enforce per-user MFA (default true)"},
    },
    "required": ["user"],
    "additionalProperties": False,
}


def describe_approval(ctx, args: dict):
    """Plain-language preview for the approval card (D-90)."""
    lic = ", ".join(str(l.get("sku")) for l in (args.get("licenses") or []) if isinstance(l, dict))
    return {
        "Onboard user": str(args.get("user") or ""),
        "Licenses": lic or "(none)",
        "Security groups": ", ".join(args.get("security_groups") or []) or "(none)",
        "Distribution groups": ", ".join(args.get("distribution_groups") or []) or "(none)",
        "Mailbox grants (Full Access + Send-As)": ", ".join(args.get("mailboxes") or []) or "(none)",
        "Contact info": "yes" if args.get("contact") else "no",
        "MFA": (("phone " + str(args.get("mfa_phone"))) if args.get("mfa_phone") else "")
               + (" + enforce" if args.get("enforce_mfa", True) else "") or "no",
    }


def run(ctx, user: str, create_first_name: str = "", create_last_name: str = "",
        create_display_name: str = "", licenses: Optional[list] = None,
        contact: Optional[dict] = None, security_groups: Optional[list] = None,
        distribution_groups: Optional[list] = None, mailboxes: Optional[list] = None,
        mfa_phone: str = "", enforce_mfa: bool = True, **_: Any):
    from ..clients.scopes import scoped_read
    from . import _graph_common as g
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a sign-in address"}

    steps: dict[str, Any] = {}
    all_ok = True

    # ── 1) ensure the account exists (configure existing; create only if non-hybrid + missing) ──
    u0 = scoped_read(ctx, "m365", f"/users/{user}", {"$select": "id,onPremisesSyncEnabled"})
    if g.fail(u0) or not (isinstance(u0, dict) and u0.get("id")):
        # user not found — is this a hybrid tenant? then the account must be made in on-prem AD
        org = scoped_read(ctx, "m365", "/organization", {"$select": "onPremisesSyncEnabled"})
        org_row = (g.rows(org) or [{}])[0] if not g.fail(org) else {}
        if org_row.get("onPremisesSyncEnabled") is True:
            return {"ok": False, "tenant_hybrid": True, "user": user,
                    "error": (f"'{user}' doesn't exist and this is a HYBRID tenant — create the "
                              f"account in on-prem Active Directory (it syncs to Entra), then "
                              f"re-run onboarding.")}
        if not (create_first_name.strip() and create_last_name.strip()):
            return {"ok": False, "user": user,
                    "error": (f"'{user}' doesn't exist. This tenant isn't hybrid, so I can create "
                              f"the account — but I need create_first_name and create_last_name "
                              f"(and you may pass create_display_name).")}
        from .m365_create_user import run as create_user
        cr = create_user(ctx, email=user, first_name=create_first_name.strip(),
                         last_name=create_last_name.strip(),
                         display_name=create_display_name.strip())
        steps["create_user"] = "done" if cr.get("ok") else cr.get("error")
        if not cr.get("ok"):
            return {"ok": False, "user": user, "steps": steps,
                    "error": "couldn't create the account — aborting onboarding (nothing else ran)"}
        hybrid = False
    else:
        hybrid = bool(u0.get("onPremisesSyncEnabled"))

    out: dict[str, Any] = {"ok": True, "user": user, "hybrid": hybrid, "steps": steps}

    def _and(ok: bool):
        nonlocal all_ok
        all_ok &= bool(ok)

    # ── 2) licenses ──
    if licenses:
        from .m365_assign_license import run as assign
        res: dict[str, Any] = {}
        for spec in licenses:
            if not isinstance(spec, dict) or not spec.get("sku"):
                continue
            sku = str(spec["sku"])
            r = assign(ctx, user=user, license=sku, disabled_apps=spec.get("disabled_apps"))
            res[sku] = "done" if r.get("ok") else r.get("error")
            _and(r.get("ok"))
        steps["licenses"] = res

    # ── 3) contact info (set_user_contact refuses on hybrid — that's a SKIP, not a failure) ──
    if contact:
        from .m365_set_user_contact import run as set_contact
        fields = {k: v for k, v in contact.items() if k in _CONTACT_FIELDS and str(v or "").strip()}
        if fields:
            r = set_contact(ctx, user=user, **fields)
            if r.get("ok"):
                steps["contact"] = "done"
            elif r.get("on_prem_synced"):
                steps["contact"] = "skipped — directory-synced; set contact info in on-prem AD"
            else:
                steps["contact"] = r.get("error")
                _and(False)

    # ── 4) groups ──
    if security_groups:
        from .m365_add_security_group_member import run as add_sec
        res = {}
        for grp in security_groups:
            r = add_sec(ctx, group=grp, member=user)
            res[grp] = "done" if r.get("ok") else r.get("error")
            _and(r.get("ok"))
        steps["security_groups"] = res
    if distribution_groups:
        from .exo_add_group_member import run as add_dl
        res = {}
        for grp in distribution_groups:
            r = add_dl(ctx, group=grp, member=user)
            res[grp] = "done" if r.get("ok") else r.get("error")
            _and(r.get("ok"))
        steps["distribution_groups"] = res

    # ── 5) mailbox access — Full Access + Send-As on each ──
    if mailboxes:
        from .exo_grant_mailbox_access import run as grant
        res = {}
        for mbx in mailboxes:
            fa = grant(ctx, mailbox=mbx, user=user, access="full_access")
            sa = grant(ctx, mailbox=mbx, user=user, access="send_as")
            if fa.get("ok") and sa.get("ok"):
                res[mbx] = "full_access + send_as"
            else:
                res[mbx] = (f"full_access: {'ok' if fa.get('ok') else fa.get('error')}; "
                            f"send_as: {'ok' if sa.get('ok') else sa.get('error')}")
                _and(False)
        steps["mailbox_access"] = res

    # ── 6) MFA phone method ──
    if str(mfa_phone or "").strip():
        from .m365_add_phone_auth import run as add_phone
        r = add_phone(ctx, user=user, phone=mfa_phone.strip(), phone_type="mobile")
        steps["mfa_phone"] = "done" if r.get("ok") else r.get("error")
        _and(r.get("ok"))

    # ── 7) enforce MFA ──
    if enforce_mfa:
        from .m365_set_mfa import run as set_mfa
        r = set_mfa(ctx, user=user, state="enforced")
        steps["enforce_mfa"] = "done" if r.get("ok") else r.get("error")
        _and(r.get("ok"))

    out["ok"] = bool(all_ok)
    if not all_ok:
        out["note"] = ("some steps failed — see `steps`; fix the cause and re-run with only the "
                       "failed parts")
    return out
