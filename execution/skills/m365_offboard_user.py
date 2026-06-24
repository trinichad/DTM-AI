"""Offboard / disable a user account — the full MSP sequence, each step optional
(D-57; reordered + hybrid-aware + group listing D-105; SOP: m365-graph)."""
from __future__ import annotations

import re
from typing import Any

NAME = "m365_offboard_user"
DESCRIPTION = ("DISABLE / OFFBOARD a user account. Runs the standard sequence IN ORDER: sign out of "
               "all devices, reset the password, block sign-in, convert the mailbox to shared, "
               "remove licenses (SKIPPED with a warning if the mailbox is over 50 GB — a license is "
               "still needed to keep that data), hide from the address book, and prefix the display "
               "name with zzz_. In a HYBRID (Entra-Connect-synced) tenant the password reset and "
               "sign-in block are mastered in on-prem AD, so they are SKIPPED with guidance. It does "
               "NOT rename the email (use exo_rename_smtp manually). It does NOT remove groups or "
               "mailbox permissions — instead it LISTS the user's distribution + security groups AND "
               "the mailboxes they have Full Access / Send-As on, so you can ask the owner which to "
               "remove (then call exo_remove_group_member / m365_remove_security_group_member / "
               "exo_revoke_mailbox_access per item). Every step is an optional flag (all on by "
               "default). Reports each step separately.")
SOURCE = "m365"
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
# declared in execution order (run() follows this order)
_FLAGS = {
    "sign_out_devices": "revoke all active sessions on every device",
    "reset_password": "scramble the password (skipped on hybrid — reset in on-prem AD)",
    "block_signin": "disable the account sign-in (skipped on hybrid — disable in on-prem AD)",
    "convert_to_shared": "convert the mailbox to a shared mailbox",
    "remove_licenses": "remove all licenses (skipped if mailbox > 50 GB)",
    "hide_from_gal": "hide from the Global Address List",
    "prefix_display_name": "prefix the display name with zzz_",
    "list_groups": "list the user's distribution + security groups for the owner to choose removals",
    "list_mailbox_access": "list the mailboxes the user has Full Access / Send-As on, for the owner "
                           "to choose revocations",
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


def _list_groups(ctx, user: str) -> dict[str, Any]:
    """READ-ONLY: the user's distribution groups (EXO, authoritative) + security groups (Graph).
    Never removes anything — the owner decides. Errors per source are reported, not fatal."""
    out: dict[str, Any] = {"distribution_groups": None, "security_groups": None, "errors": {}}
    try:
        from .exo_user_distribution_groups import memberships
        dl = memberships(ctx, user)
        if dl.get("ok"):
            out["distribution_groups"] = dl["groups"]
        else:
            out["errors"]["distribution"] = dl.get("error")
    except Exception as e:                            # noqa: BLE001 — e.g. no EXO connection
        out["errors"]["distribution"] = str(e)[:200]
    try:
        from .m365_user_groups import run as user_groups
        ug = user_groups(ctx, user=user)
        if ug.get("ok"):
            out["security_groups"] = [x for x in ug["groups"] if x.get("kind") == "security"]
        else:
            out["errors"]["security"] = ug.get("error")
    except Exception as e:                            # noqa: BLE001
        out["errors"]["security"] = str(e)[:200]
    return out


def _list_mailbox_access(ctx, user: str) -> dict[str, Any]:
    """READ-ONLY: the mailboxes the user has Full Access / Send-As / Send-on-Behalf on. The mirror
    of the access onboard GRANTS — surfaced for the owner to revoke. Never revokes anything."""
    try:
        from .exo_user_mailbox_access import run as mbx_access
        r = mbx_access(ctx, user=user)                    # full sweep (no cap) for offboarding
        if r.get("ok"):
            return {"mailboxes": r.get("mailboxes") or [], "checked": r.get("mailboxes_checked")}
        return {"error": r.get("error")}
    except Exception as e:                            # noqa: BLE001 — e.g. no EXO connection
        return {"error": str(e)[:200]}


def run(ctx, user: str, sign_out_devices: bool = True, reset_password: bool = True,
        block_signin: bool = True, convert_to_shared: bool = True, remove_licenses: bool = True,
        hide_from_gal: bool = True, prefix_display_name: bool = True, list_groups: bool = True,
        list_mailbox_access: bool = True, **_: Any):
    from ..clients.scopes import scoped_read, scoped_write
    from . import _exo_common as c
    from . import _graph_common as g
    from .m365_create_user import _gen_password
    user = (user or "").strip()
    if "@" not in user:
        return {"ok": False, "error": f"'{user}' is not a sign-in address"}

    # Resolve id + the HYBRID flag up front. The object id keeps Graph steps stable; onPremisesSync
    # tells us whether sign-in/password are mastered on-prem (then they're skipped, not failed).
    u0 = scoped_read(ctx, "m365", f"/users/{user}",
                     {"$select": "id,userPrincipalName,onPremisesSyncEnabled"})
    bad = g.fail(u0)
    if bad:
        return bad
    uid = str((u0 or {}).get("id") or "")
    if not uid:
        return {"ok": False, "error": f"no user '{user}' found in this client"}
    hybrid = bool(u0.get("onPremisesSyncEnabled"))

    steps: dict[str, Any] = {}
    warnings: list[str] = []
    all_ok = True

    # 1) sign out of every device (cloud session revoke — valid in hybrid too)
    if sign_out_devices:
        all_ok &= _step_graph(steps, "sign_out_devices", lambda: scoped_write(
            ctx, "m365", f"/users/{uid}/revokeSignInSessions", body={}, method="POST"))

    # 2) reset password — mastered in on-prem AD when hybrid, so skip with guidance there
    if reset_password:
        if hybrid:
            steps["reset_password"] = ("skipped — directory-synced (hybrid); reset the password in "
                                       "on-prem Active Directory (it syncs to Entra)")
        else:
            all_ok &= _step_graph(steps, "reset_password", lambda: scoped_write(
                ctx, "m365", f"/users/{uid}",
                body={"passwordProfile": {"password": _gen_password(),
                                          "forceChangePasswordNextSignIn": True}},
                method="PATCH"),
                hint_403="changing another user's password needs User-PasswordProfile.ReadWrite.All "
                         "in M365_SCOPES (re-sign-in after adding) AND a User Administrator role")

    # 3) block sign-in — mastered in on-prem AD when hybrid (disable there, it syncs)
    if block_signin:
        if hybrid:
            steps["block_signin"] = ("skipped — directory-synced (hybrid); disable the account in "
                                     "on-prem Active Directory (it syncs to Entra and blocks "
                                     "sign-in)")
        else:
            all_ok &= _step_graph(steps, "block_signin", lambda: scoped_write(
                ctx, "m365", f"/users/{uid}", body={"accountEnabled": False}, method="PATCH"))

    # ── Mailbox steps (EXO, by the original address) ─────────────────────────────────────
    needs_exo = convert_to_shared or remove_licenses or hide_from_gal
    mb = None
    exo = None
    if needs_exo:
        try:
            exo = ctx.client("exo")
            mb, bad = c.get_one_mailbox(exo, user)
        except Exception as e:                        # noqa: BLE001 — no EXO connection
            bad = {"error": str(e)[:200]}
        if bad or mb is None:
            msg = (bad or {}).get("error", "mailbox not found")
            for flag, on in (("convert_to_shared", convert_to_shared),
                             ("hide_from_gal", hide_from_gal)):
                if on:
                    steps[flag] = f"skipped — {msg}"
            if remove_licenses:
                # without the mailbox we can't run the 50 GB data-retention check,
                # so removing licenses blind could strand the data — skip it too
                steps["remove_licenses"] = f"skipped — can't check the mailbox size ({msg})"
                remove_licenses = False
            warnings.append(f"mailbox steps skipped: {msg}")
            mb = None

    # 4) convert to shared (async — Set + poll, D-104b)
    if mb is not None and convert_to_shared:
        if str(mb.get("RecipientTypeDetails")) == "SharedMailbox":
            steps["convert_to_shared"] = "already shared"
        else:
            rset = exo.invoke("Set-Mailbox", {"Identity": user, "Type": "Shared", "Confirm": False})
            if c.err(rset):
                steps["convert_to_shared"] = c.err(rset)
                all_ok = False
            else:
                flipped, _ = c.settle(
                    lambda: c.get_one_mailbox(exo, user)[0] or {},
                    lambda m: str(m.get("RecipientTypeDetails")) == "SharedMailbox")
                steps["convert_to_shared"] = ("done" if flipped else
                    "accepted but not yet propagated — re-check with exo_mailbox_details shortly")
                all_ok &= flipped

    # 5) remove licenses (with the 50 GB data-retention safeguard)
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
                # assignedLicenses lags the POST — poll until empty before failing (D-104).
                cleared, check = g.settle(
                    lambda: scoped_read(ctx, "m365", f"/users/{uid}",
                                        {"$select": "assignedLicenses"}),
                    lambda c: not g.fail(c)
                    and len((c or {}).get("assignedLicenses") or []) == 0)
                if cleared:
                    return {"removed": len(skus)}
                left = len((check or {}).get("assignedLicenses") or [])
                return {"error": f"{left} license(s) still show after removal — usually "
                                 f"propagation lag; re-check with m365_list_user_license_assignments"}
            ok = _step_graph(steps, "remove_licenses", _remove)
            if ok and steps["remove_licenses"] == "done":
                steps["remove_licenses"] = "done (all licenses removed)"
            all_ok &= ok

    # 6) hide from the GAL
    if mb is not None and hide_from_gal:
        r = c.set_and_verify(exo, user, {"HiddenFromAddressListsEnabled": True},
                             {"HiddenFromAddressListsEnabled": True}, label="hide")
        steps["hide_from_gal"] = "done" if r.get("ok") else r.get("error")
        all_ok &= bool(r.get("ok"))

    # 7) prefix the display name with zzz_ — display name is AD-mastered on hybrid (like the
    #    password/sign-in steps), so skip with guidance there instead of failing on a 400 (D-108).
    if prefix_display_name:
        if hybrid:
            steps["prefix_display_name"] = ("skipped — directory-synced (hybrid); the display name "
                                            "is mastered in on-prem Active Directory — rename it "
                                            "there if you want the zzz_ marker")
        else:
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

    out: dict[str, Any] = {"ok": bool(all_ok), "user": user, "hybrid": hybrid, "steps": steps}

    # 8) list groups for the owner to decide on — READ-ONLY, never auto-removed (best-effort)
    if list_groups:
        gc = _list_groups(ctx, user)
        dg, sg = gc["distribution_groups"], gc["security_groups"]
        steps["list_groups"] = (f"found {len(dg or [])} distribution + {len(sg or [])} security "
                                f"group(s)" if (dg is not None or sg is not None)
                                else "could not list groups")
        out["group_cleanup"] = {
            "distribution_groups": dg or [],
            "security_groups": sg or [],
            **({"errors": gc["errors"]} if gc["errors"] else {}),
            "instruction": (f"GROUP CLEANUP IS NOT AUTOMATIC and nothing here was removed. Ask the "
                            f"owner which of these groups to remove {user} from — ALL, SOME, or "
                            f"NONE. For each chosen DISTRIBUTION group call exo_remove_group_member; "
                            f"for each chosen SECURITY group call m365_remove_security_group_member "
                            f"— one call per group, each its own approval. Dynamic groups can't be "
                            f"removed manually."),
        }

    # 9) list mailbox access (Full Access / Send-As) for the owner to decide on — READ-ONLY
    if list_mailbox_access:
        ma = _list_mailbox_access(ctx, user)
        if "error" in ma:
            steps["list_mailbox_access"] = f"could not list: {ma['error']}"
        else:
            mboxes = ma["mailboxes"]
            steps["list_mailbox_access"] = f"found {len(mboxes)} mailbox grant(s)"
            out["mailbox_access_cleanup"] = {
                "mailboxes": mboxes,
                "instruction": (f"MAILBOX-ACCESS CLEANUP IS NOT AUTOMATIC and nothing here was "
                                f"removed. Ask the owner which of these mailboxes to revoke {user}'s "
                                f"access on — ALL, SOME, or NONE. For each, call "
                                f"exo_revoke_mailbox_access(mailbox, user={user}, access=<one of the "
                                f"listed: full_access | send_as | send_on_behalf>) — one call per "
                                f"access type per mailbox, each its own approval."),
            }

    if warnings:
        out["warnings"] = warnings
    if not all_ok:
        out["note"] = ("some steps failed or were skipped — see `steps`; re-run with only the "
                       "failed flags after fixing the cause")
    return out
