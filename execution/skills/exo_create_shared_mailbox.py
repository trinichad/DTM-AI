"""Create an Exchange Online SHARED mailbox + delegate rights (D-41; SOP: exchange-online)."""
from __future__ import annotations

from typing import Any

NAME = "exo_create_shared_mailbox"
DESCRIPTION = ("Create a SHARED mailbox in the client's Exchange Online (no license needed) and "
               "optionally grant a user Full Access (with Outlook automapping) and/or Send As. "
               "Give the primary email address, a display name, and the delegate's UPN(s). "
               "ALWAYS ask the user for first_name/last_name if they didn't give them — never "
               "invent them and never omit them unless the user explicitly says to skip. "
               "Requires the client's Exchange connection (M365 card) and owner approval per run.")
SOURCE = "m365"              # grouped with the other Office 365 tools; client is ctx.client("exo")
CATEGORY = "write"
RISK_LEVEL = "high"
REQUIRES_APPROVAL = True
ENABLED_BY_DEFAULT = False
PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "email": {"type": "string", "description": "primary SMTP address for the new shared "
                                                   "mailbox, e.g. shared@demodomain.com"},
        "display_name": {"type": "string", "description": "display name (defaults to the part "
                                                          "before @)"},
        "first_name": {"type": "string", "description": "first name — ask the user if not given"},
        "last_name": {"type": "string", "description": "last name — ask the user if not given"},
        "full_access_to": {"type": "string", "description": "UPN to grant Full Access (optional)"},
        "send_as_to": {"type": "string", "description": "UPN to grant Send As (optional)"},
        "automap": {"type": "boolean", "description": "auto-map the mailbox into the delegate's "
                                                      "Outlook (default true)"},
    },
    "required": ["email"],
    "additionalProperties": False,
}

def _err(result: Any) -> str:
    return str(result.get("error")) if isinstance(result, dict) and result.get("error") else ""


def run(ctx, email: str, display_name: str = "", first_name: str = "", last_name: str = "",
        full_access_to: str = "", send_as_to: str = "", automap: bool = True, **_: Any):
    email = (email or "").strip()
    if "@" not in email or " " in email:
        return {"ok": False, "error": f"'{email}' is not a valid email address"}
    name = (display_name or "").strip() or email.split("@")[0]
    exo = ctx.client("exo")

    # Pin the User ID (UPN) and Alias to the requested address — without these Exchange
    # derives them from Name ("AI Test" → AITest@…) and the User ID diverges from the email.
    # NB: -UserPrincipalName is the sign-in parameter in New-Mailbox's Shared parameter set;
    # -MicrosoftOnlineServicesID lives in a DIFFERENT set and conflicts with -Shared (D-66).
    params: dict[str, Any] = {"Name": name, "DisplayName": name, "PrimarySmtpAddress": email,
                              "UserPrincipalName": email, "Alias": email.split("@")[0]}
    if (first_name or "").strip():
        params["FirstName"] = first_name.strip()
    if (last_name or "").strip():
        params["LastName"] = last_name.strip()
    created = exo.invoke("New-Mailbox", params)
    if _err(created):
        return {"ok": False, "step": "create", "error": _err(created)}
    steps: dict[str, Any] = {"created": email}

    failures = []
    if (full_access_to or "").strip():
        r = exo.invoke("Add-MailboxPermission", {
            "Identity": email, "User": full_access_to.strip(),
            "AccessRights": ["FullAccess"], "AutoMapping": bool(automap)})
        e = _err(r)
        steps["full_access"] = e or f"granted to {full_access_to.strip()}"
        if e:
            failures.append("full_access")
    if (send_as_to or "").strip():
        r = exo.invoke("Add-RecipientPermission", {
            "Identity": email, "Trustee": send_as_to.strip(),
            "AccessRights": ["SendAs"], "Confirm": False})
        e = _err(r)
        steps["send_as"] = e or f"granted to {send_as_to.strip()}"
        if e:
            failures.append("send_as")

    return {"ok": not failures, **steps,
            **({"note": "mailbox was created but some grants failed — see the failed steps"}
               if failures else {})}
