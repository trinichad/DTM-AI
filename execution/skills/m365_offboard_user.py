"""Offboard / disable a user account — the full MSP sequence, each step optional
(D-57; SOP: m365-graph)."""
from __future__ import annotations

import re
from typing import Any

NAME = "m365_offboard_user"
DESCRIPTION = ("DISABLE / OFFBOARD a user account. Runs the standard sequence — block sign-in, "
               "sign out of all devices, scramble the password, convert the mailbox to shared, "
               "remove licenses (SKIPPED with a warning if the mailbox is over 50 GB — a "
               "license is still needed to keep that data), hide from the address book, "
               "rename the email to zzz_<old> so new mail BOUNCES, and prefix the display "
               "name with zzz_ so IT can spot disabled accounts. EVERY step is an optional "
               "flag (all on by default) — turn off any the user doesn't want. Reports each "
               "step's outcome separately.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
_FLAGS = {
    "block_signin": "disable the account sign-in",
    "sign_out_devices": "revoke all active sessions on every device",
    "reset_password": "scramble the password (not shown to anyone)",
    "convert_to_shared": "convert the mailbox to a shared mailbox",
    "remove_licenses": "remove all licenses (skipped if mailbox > 50 GB)",
    "hide_from_gal": "hide from the Global Address List",
    "rename_smtp": "rename email to zzz_<old> and drop the old address (mail bounces)",
    "prefix_display_name": "prefix the display name with zzz_",
}
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "user": {"type": "string", "description": "the user's sign-in address (UPN)"},
        **{f: {"type": "boolean", "description": d + " (default true)"}
           for f, d in _FLAGS.items()},
    },
    "required": ["user"],
    "additionalProperties": False,
}

_50GB = 50 * 1024 ** 3


def _bytes(size: Any) -> int:
    """'49.5 GB (53,150,220,288 bytes)' → 53150220288; unparsable → -1."""
    m = re.search(r"\(([\d,]+)\s*bytes\)", str(size or ""))
    return int(m.group(1).replace(",", "")) if m else -1


def _step_graph(steps: dict, name: str, fn, hint_403: str = ""):
    from ..clients._http import HttpError
    from . import _graph_common as g
    try:
        r = fn()
        bad = g.fail(r)
        steps[name] = bad["error"] if bad else "done"
        return not bad
    except HttpError as e:                            # noqa: BLE001
        hint = f" — {hint_403}" if (e.status == 403 and hint_403) else ""
        steps[name] = f"Graph HTTP {e.status}: {e.body[:200]}{hint}"
        return False
    except Exception as e:                            # noqa: BLE001
        steps[name] = str(e)[:200]
        return False


def run(ctx, user: str, block_signin: bool = True, sign_out_devices: bool = True,
        reset_password: bool = True, convert_to_shared: bool = True,
        remove_licenses: bool = True, hide_from_gal: bool = True,
        rename_smtp: bool = True, prefix_display_name: bool = True, **_: Any):
    from ..clients.scopes import scoped_read, scoped_write
    from . import _exo_common as c
    from . import _graph_common as g
    from .m365_create_user import _gen_password
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a sign-in address"}

    # Resolve the OBJECT ID up front — Graph steps keep working after the UPN rename.
    uid, bad = g.resolve_user_id(ctx, user)
    if bad:
        return bad
    steps: dict[str, Any] = {}
    warnings: list[str] = []
    all_ok = True

    # ── Graph identity steps (by object id) ──────────────────────────────────────────────
    if block_signin:
        all_ok &= _step_graph(steps, "block_signin", lambda: scoped_write(
            ctx, "m365", f"/users/{uid}", body={"accountEnabled": False}, method="PATCH"))
    if sign_out_devices:
        all_ok &= _step_graph(steps, "sign_out_devices", lambda: scoped_write(
            ctx, "m365", f"/users/{uid}/revokeSignInSessions", body={}, method="POST"))
    if reset_password:
        # scrambled on purpose — the new password is NOT returned to anyone
        all_ok &= _step_graph(steps, "reset_password", lambda: scoped_write(
            ctx, "m365", f"/users/{uid}",
            body={"passwordProfile": {"password": _gen_password(),
                                      "forceChangePasswordNextSignIn": True}},
            method="PATCH"),
            hint_403="changing another user's password needs the User-PasswordProfile."
                     "ReadWrite.All scope in M365_SCOPES (re-sign-in after adding) AND the "
                     "signing admin must be at least a User Administrator")

    # ── Mailbox steps (EXO, by the original address) ─────────────────────────────────────
    needs_exo = convert_to_shared or remove_licenses or hide_from_gal or rename_smtp
    mb = None
    if needs_exo:
        try:
            exo = ctx.client("exo")
            mb, bad = c.get_one_mailbox(exo, user)
        except Exception as e:                        # noqa: BLE001 — no EXO connection
            bad = {"error": str(e)[:200]}
        if bad or mb is None:
            msg = (bad or {}).get("error", "mailbox not found")
            for flag, on in (("convert_to_shared", convert_to_shared),
                             ("hide_from_gal", hide_from_gal),
                             ("rename_smtp", rename_smtp)):
                if on:
                    steps[flag] = f"skipped — {msg}"
            if remove_licenses:
                # without the mailbox we can't run the 50 GB data-retention check,
                # so removing licenses blind could strand the data — skip it too
                steps["remove_licenses"] = f"skipped — can't check the mailbox size ({msg})"
                remove_licenses = False
            warnings.append(f"mailbox steps skipped: {msg}")
            mb = None

    if mb is not None and convert_to_shared:
        if str(mb.get("RecipientTypeDetails")) == "SharedMailbox":
            steps["convert_to_shared"] = "already shared"
        else:
            r = c.set_and_verify(exo, user, {"Type": "Shared"},
                                 {"RecipientTypeDetails": "SharedMailbox"}, label="convert")
            steps["convert_to_shared"] = "done" if r.get("ok") else r.get("error")
            all_ok &= bool(r.get("ok"))

    if remove_licenses:
        too_big = False
        if mb is not None:
            stats = exo.invoke("Get-MailboxStatistics", {"Identity": user})
            row = stats[0] if isinstance(stats, list) and stats else stats
            size = _bytes(row.get("TotalItemSize")) if isinstance(row, dict) else -1
            if size > _50GB:
                too_big = True
                gb = round(size / 1024 ** 3, 1)
                steps["remove_licenses"] = f"SKIPPED — mailbox is {gb} GB (over 50 GB)"
                warnings.append(f"licenses kept: the mailbox is {gb} GB — shared mailboxes "
                                f"over 50 GB still require a license to keep the data")
        if not too_big:
            def _remove():
                u = scoped_read(ctx, "m365", f"/users/{uid}",
                                {"$select": "assignedLicenses"})
                bad2 = g.fail(u)
                if bad2:
                    return bad2
                skus = [str(l.get("skuId")) for l in (u.get("assignedLicenses") or [])
                        if isinstance(l, dict) and l.get("skuId")]
                if not skus:
                    return {"none": True}
                r = scoped_write(ctx, "m365", f"/users/{uid}/assignLicense",
                                 body={"addLicenses": [], "removeLicenses": skus},
                                 method="POST")
                bad2 = g.fail(r)
                if bad2:
                    return bad2
                check = scoped_read(ctx, "m365", f"/users/{uid}",
                                    {"$select": "assignedLicenses"})
                left = len((check or {}).get("assignedLicenses") or [])
                return ({"removed": len(skus)} if left == 0 else
                        {"error": f"{left} license(s) still on the user after removal"})
            ok = _step_graph(steps, "remove_licenses", _remove)
            if ok and steps["remove_licenses"] == "done":
                steps["remove_licenses"] = "done (all licenses removed)"
            all_ok &= ok

    if mb is not None and hide_from_gal:
        r = c.set_and_verify(exo, user, {"HiddenFromAddressListsEnabled": True},
                             {"HiddenFromAddressListsEnabled": True}, label="hide")
        steps["hide_from_gal"] = "done" if r.get("ok") else r.get("error")
        all_ok &= bool(r.get("ok"))

    new_address = None
    if mb is not None and rename_smtp:
        from ..clients.exo import hashtable
        local, domain = user.split("@", 1)
        new_address = f"zzz_{local}@{domain}"
        r = exo.invoke("Set-Mailbox", {"Identity": user, "Confirm": False,
                                       "WindowsEmailAddress": new_address,
                                       "MicrosoftOnlineServicesID": new_address})
        if c.err(r):
            steps["rename_smtp"] = c.err(r)
            all_ok = False
        else:
            r2 = exo.invoke("Set-Mailbox", {"Identity": new_address, "Confirm": False,
                                            "EmailAddresses":
                                            hashtable({"Remove": f"smtp:{user}"})})
            after, bad = c.get_one_mailbox(exo, new_address)
            old_gone = after is not None and not any(
                str(a).lower() == f"smtp:{user.lower()}"
                for a in (after.get("EmailAddresses") or []))
            if c.err(r2) or not old_gone:
                steps["rename_smtp"] = (f"renamed to {new_address} but the OLD address may "
                                        f"still be attached — mail might not bounce; check "
                                        f"Exchange ({c.err(r2) or 'old alias still listed'})")
                all_ok = False
            else:
                steps["rename_smtp"] = f"done — now {new_address}; mail to {user} bounces"

    if prefix_display_name:
        def _prefix():
            u = scoped_read(ctx, "m365", f"/users/{uid}", {"$select": "displayName"})
            bad2 = g.fail(u)
            if bad2:
                return bad2
            name = str((u or {}).get("displayName") or "")
            if name.startswith("zzz_"):
                return {"already": True}
            return scoped_write(ctx, "m365", f"/users/{uid}",
                                body={"displayName": f"zzz_{name}"}, method="PATCH")
        all_ok &= _step_graph(steps, "prefix_display_name", _prefix)

    out: dict[str, Any] = {"ok": bool(all_ok), "user": user, "steps": steps}
    if new_address and "done" in str(steps.get("rename_smtp", "")):
        out["new_address"] = new_address
    if warnings:
        out["warnings"] = warnings
    if not all_ok:
        out["note"] = "some steps failed or were skipped — see `steps`; re-run with only " \
                      "the failed flags after fixing the cause"
    return out
